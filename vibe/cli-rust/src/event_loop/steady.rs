//! Steady-state event selection.

use anyhow::Result;

use crate::app::Status;
use crate::{completion_manager, event_handler, turn_summary, ui};

use super::deadlines::{approval_pause_deadline, typing_pause_deadline};
use super::external_editor::open_external_editor;
use super::handoff::TerminalHandoff;
use super::helpers::{apply_command, frame_settled, redraw_frame};
use super::keys::{handle_input_batch, next_input, LoopState};
use super::notifications::{absorb_notification, surface_server_close};
use super::suspend::suspend_once;
use super::{EventLoop, LoopExit, REDRAW_INTERVAL};
use crate::clock;

impl EventLoop {
    pub(super) async fn steady(&mut self) -> Result<LoopExit> {
        let sources = &mut self.sources;
        let input = &mut self.input;
        let shutdown = &mut self.shutdown;
        let replaying = crate::utils::is_replaying();
        // Animations run on UI time; their arms precede the input's, so a tick an advance made due lands before the next fence.
        let mut frame = clock::interval(ui::banner::petit_chat::FRAME_INTERVAL);
        let mut blink = clock::interval(std::time::Duration::from_millis(500));
        let mut spin = clock::interval(ui::pulse::TICK_INTERVAL);
        let mut narrator_anim = clock::interval(ui::narrator::ANIMATION_INTERVAL);
        let mut voice_tick = clock::interval_real(std::time::Duration::from_millis(50));
        let mut selection_scroll = clock::interval_real(std::time::Duration::from_millis(50));
        let mut redraw_tick = clock::interval_real(REDRAW_INTERVAL);
        let mut state = LoopState::new();
        let mut deferred_notification = None;

        loop {
            if self.app.suspend_requested {
                self.app.suspend_requested = false;
                suspend_once(&mut self.app, &mut self.terminal, &mut self.terminal_guard)?;
                continue;
            }
            if self.app.external_editor_requested {
                self.app.external_editor_requested = false;
                let handoff = TerminalHandoff {
                    terminal: &mut self.terminal,
                    guard: &mut self.terminal_guard,
                    reader: &self.input_reader,
                };
                let quit = open_external_editor(
                    &mut self.app,
                    &self.client,
                    handoff,
                    sources,
                    shutdown,
                    &mut deferred_notification,
                )
                .await?;
                if quit {
                    return Ok(LoopExit::Quit);
                }
                state.redraw_pending = true;
                continue;
            }
            if self.app.approval.has_capacity() && crate::question_app::has_capacity(&self.app) {
                if let Some(notification) = deferred_notification.take() {
                    if !event_handler::apply_notification(
                        &mut self.app,
                        &self.client,
                        &notification,
                    ) {
                        deferred_notification = Some(notification);
                    }
                    state.redraw_pending = true;
                }
            }
            tokio::select! {
                biased;
                // First: a signal must land even while other sources are
                // continuously ready, and Ctrl+Z suspension must not leave a
                // Burst backlog of missed animation ticks.
                _ = shutdown.wait() => return Ok(LoopExit::Quit),
                _ = selection_scroll.tick(), if crate::selection::is_auto_scrolling(&self.app)
                    || crate::mouse::is_track_paging(&self.app) =>
                {
                    if crate::selection::is_auto_scrolling(&self.app) {
                        crate::selection::auto_scroll(&mut self.app);
                    }
                    crate::mouse::repeat_track_page(&mut self.app);
                    state.redraw_pending = true;
                }
                // A drawn frame is always behind the loop: the keyed launch
                // painted the unready frame, the replay fold drew its
                // convergence, and every wizard round painted its relaunch
                // before re-entering.
                _ = redraw_tick.tick(), if state.redraw_pending => {
                    let mouse = self.app.session.startup_config.enable_mouse_capture;
                    self.terminal_guard.set_mouse_capture(mouse)?;
                    let (animating, mounting) = redraw_frame(
                        &mut self.app,
                        &self.client,
                        &mut self.terminal,
                        replaying,
                    )?;
                    if mounting {
                        // Slow draws must leave time for input and newer session replies.
                        redraw_tick.reset();
                    }
                    // Read idle first: tracked work ends only after it queues its result or publishes an index version.
                    crate::replay_fence::ack_if(|| {
                        crate::work::idle()
                            && sources.is_drained(deferred_notification.is_some())
                            && !self.app.completion.files.has_update()
                            && state.deferred_input.is_none()
                            && frame_settled(&self.app, &self.client, animating, mounting)
                    });
                    state.redraw_pending = animating || mounting;
                    continue;
                }
                // The last tracked task may end without sending anything: re-check the ack.
                _ = crate::work::finished(), if replaying => {
                    state.redraw_pending = true;
                }
                _ = frame.tick() => {
                    if self.app.terminal_notifier.presentation_changed() {
                        crate::terminal_notifier::flush(&mut self.app);
                    }
                    let changed = !self.app.session.startup_config.disable_welcome_banner_animation
                        && self.app.view.banner.tick(crate::clock::now());
                    state.redraw_pending |= changed;
                }
                _ = spin.tick() => {
                    let subagent_loading = self
                        .app
                        .subagents
                        .viewed_child()
                        .is_some_and(|child| crate::subagents::is_active(child.status));
                    let active = self.app.view.command_loading
                        || self.app.compacting
                        || subagent_loading
                        || crate::commands::stress::running(&self.app)
                        || matches!(self.app.session.status, Status::Starting | Status::Generating { .. });
                    if active {
                        self.app.view.pulse_frame = self.app.view.pulse_frame.wrapping_add(1);
                        self.app.view.loading.tick();
                    }
                    let switching = self.app.agents.switching_indicator;
                    if switching {
                        self.app.agents.frame = self.app.agents.frame.wrapping_add(1);
                    }
                    // The index thread has no dedicated source: its version
                    // feed is polled here, so a fresh index repaints an open
                    // `@` popup within one tick.
                    if completion_manager::sync_files(&mut self.app) {
                        state.redraw_pending = true;
                    }
                    state.redraw_pending |= active || switching;
                }
                _ = blink.tick(), if self.app.view.app_focus => {
                    self.app.view.cursor_on = !self.app.view.cursor_on;
                    state.redraw_pending = true;
                }
                _ = voice_tick.tick() => {
                    if self.app.recording_active() {
                        self.app.voice.frame = self.app.voice.frame.wrapping_add(1);
                        state.redraw_pending = true;
                    }
                }
                _ = narrator_anim.tick(), if self.app.narrator.state != turn_summary::NarratorState::Idle => {
                    self.app.narrator.frame = self.app.narrator.frame.wrapping_add(1);
                    state.redraw_pending = true;
                }
                Some(ev) = sources.voice.recv() => {
                    self.app.apply_voice_event(ev);
                    state.redraw_pending = true;
                }
                // Draws nothing and settles nothing: analytics never alter the UI.
                // Pre-session events are held until `set_session_id` flushes them.
                Some(event) = sources.telemetry.recv() => {
                    if let Some(session_id) = self.app.session.session_id.clone() {
                        crate::telemetry::send(&self.client, &session_id, event);
                    } else {
                        self.app.session.held_telemetry.push(event);
                    }
                    // Draws nothing, but an emptied channel may be the last thing an ack waits for.
                    state.redraw_pending |= replaying;
                }
                Some(event) = sources.ready.recv() => {
                    // The missing-key verdict is a typed loop exit (the
                    // wizard is a pre-session surface): the loop unwinds and
                    // the entrypoint reruns the
                    // wizard against a fresh child, seeded for the provider
                    // the verdict names.
                    if let crate::startup::StartupEvent::MissingApiKey { provider, env_key } =
                        event
                    {
                        return Ok(LoopExit::NeedsOnboarding { provider, env_key });
                    }
                    if crate::event_handler::apply_startup_event(
                        &mut self.app,
                        &self.client,
                        &self.config_tx,
                        event,
                    ) {
                        return Ok(LoopExit::Quit);
                    }
                    if self.app.trust.open {
                        // Keys typed at the chat frame must not answer a gate
                        // the user has not seen yet; replay fences still count.
                        while let Ok(event) = input.try_recv() {
                            crate::replay_fence::observe(&event);
                        }
                        state.deferred_input = None;
                        // The drain may have dropped a resize.
                        let _ = crate::terminal::sync_pixel_reports();
                    }
                    state.redraw_pending = true;
                }
                Some(notification) = sources.notifications.recv(), if deferred_notification.is_none() => {
                    absorb_notification(
                        &mut self.app,
                        &self.client,
                        sources,
                        notification,
                        &mut deferred_notification,
                    );
                    state.redraw_pending = true;
                }
                Some(loaded) = sources.config.recv() => {
                    crate::config::apply_loaded(&mut self.app, loaded);
                    state.redraw_pending = true;
                }
                _ = clock::sleep_until_real(self.app.theme_picker.preview_at) => {
                    crate::theme_picker::preview(&mut self.app);
                    state.redraw_pending = true;
                }
                _ = clock::sleep_until(typing_pause_deadline(&self.app)), if crate::question_app::waiting(&self.app) => {
                    crate::question_app::show_pending(&mut self.app);
                    state.redraw_pending = true;
                }
                _ = clock::sleep_until(approval_pause_deadline(&self.app)), if crate::approval::can_wake(&self.app) => {
                    crate::approval::show_pending(&mut self.app);
                    state.redraw_pending = true;
                }
                _ = clock::sleep_until(self.app.feedback.hide_at) => {
                    crate::feedback::hide_expired(&mut self.app);
                    state.redraw_pending = true;
                }
                _ = clock::sleep_until_real(self.app.subagents.refresh_at) => {
                    // One fetch per 50ms window, even while updates stream.
                    crate::subagents::start_refresh(&mut self.app, &self.client);
                    state.redraw_pending = true;
                }
                Some(event) = sources.events.recv() => {
                    crate::ui_event::apply(&mut self.app, &self.client, event);
                    state.redraw_pending = true;
                }
                _ = clock::sleep_until(self.app.agents.spinner_at) => {
                    crate::agents::show_switch_spinner(&mut self.app);
                    state.redraw_pending = true;
                }
                _ = clock::sleep_until(self.app.mcp.refresh_at), if self.app.mcp.open => {
                    crate::mcp::background_refresh(&mut self.app, &self.client);
                }
                Some(command) = sources.commands.recv() => {
                    apply_command(&mut self.app, &self.client, command);
                    state.redraw_pending = true;
                }
                event = next_input(input, &mut state.deferred_input) => match handle_input_batch(
                    &mut self.app,
                    &self.client,
                    &self.config_tx,
                    input,
                    event,
                    &mut state,
                ) {
                    None => return Ok(LoopExit::Quit),
                    Some(reset_blink) => {
                        if reset_blink {
                            blink.reset();
                        }
                    }
                },
                changed = self.crash_rx.changed(), if !self.app.server_closed => {
                    if surface_server_close(&mut self.app, changed.is_ok() && *self.crash_rx.borrow()) {
                        state.redraw_pending = true;
                    }
                }
            }
        }
    }
}
