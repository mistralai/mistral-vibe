//! Steady-state event selection.

use anyhow::Result;

use crate::app::Status;
use crate::{completion_manager, event_handler, turn_summary, ui};

use super::deadlines::{
    approval_pause_deadline, feedback_deadline, preview_deadline, spinner_deadline,
    subagent_refresh_deadline, typing_pause_deadline,
};
use super::external_editor::open_external_editor;
use super::handoff::TerminalHandoff;
use super::helpers::{apply_command, redraw_frame, replay_marker};
use super::keys::{handle_input_event, InputOutcome, LoopState};
use super::notifications::{absorb_notification, surface_server_close};
use super::suspend::suspend_once;
use super::{EventLoop, LoopExit, REDRAW_INTERVAL};

impl EventLoop {
    pub(super) async fn steady(&mut self) -> Result<LoopExit> {
        let sources = &mut self.sources;
        let input = &mut self.input;
        let shutdown = &mut self.shutdown;
        let mut frame = tokio::time::interval(ui::banner::petit_chat::FRAME_INTERVAL);
        frame.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let replaying = std::env::var_os("VIBE_REPLAY_FIXTURE").is_some();
        let settle_busy = std::env::var_os("VIBE_REPLAY_SETTLE_BUSY").is_some();
        let mut blink = tokio::time::interval(std::time::Duration::from_millis(500));
        blink.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut spin = tokio::time::interval(ui::pulse::TICK_INTERVAL);
        spin.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut narrator_anim = tokio::time::interval(ui::narrator::ANIMATION_INTERVAL);
        narrator_anim.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut voice_tick = tokio::time::interval(std::time::Duration::from_millis(50));
        voice_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut selection_scroll = tokio::time::interval(std::time::Duration::from_millis(50));
        selection_scroll.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut redraw_tick = tokio::time::interval(REDRAW_INTERVAL);
        redraw_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut state = LoopState::new();
        let mut file_watch_open = true;
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
                state.idle_marker_emitted = false;
                state.redraw_pending = true;
                state.real_event_pending = true;
                continue;
            }
            if self.app.approval.has_capacity() {
                if let Some(notification) = deferred_notification.take() {
                    if !event_handler::apply_notification(
                        &mut self.app,
                        &self.client,
                        &notification,
                    ) {
                        deferred_notification = Some(notification);
                    }
                    state.redraw_pending = true;
                    state.real_event_pending = true;
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
                    state.idle_marker_emitted = false;
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                // A drawn frame is always behind the loop: the keyed launch
                // painted the unready frame, the replay fold drew its
                // convergence, and every wizard round painted its relaunch
                // before re-entering.
                _ = redraw_tick.tick(), if state.redraw_pending => {
                    let (animating, mounting, indexing) = redraw_frame(
                        &mut self.app,
                        &self.client,
                        &mut sources.files,
                        &mut self.terminal,
                        replaying,
                    )?;
                    if mounting {
                        // Slow draws must leave time for input and newer session replies.
                        redraw_tick.reset();
                    }
                    replay_marker(
                        &self.app,
                        &mut state,
                        replaying,
                        settle_busy,
                        animating,
                        mounting,
                        indexing,
                    );
                    state.redraw_pending = animating || mounting;
                    state.real_event_pending &= mounting || animating;
                    continue;
                }
                _ = frame.tick() => {
                    let changed = !replaying
                        && !self.app.session.startup_config.disable_welcome_banner_animation
                        && self.app.view.banner.tick(std::time::Instant::now());
                    state.redraw_pending |= changed;
                }
                _ = spin.tick() => {
                    let subagent_loading = self
                        .app
                        .subagents
                        .viewed_child()
                        .is_some_and(|child| crate::subagents::is_active(child.status));
                    let active = !settle_busy
                        && (self.app.view.command_loading
                            || self.app.compacting
                            || subagent_loading
                            || crate::teleport::busy(&self.app)
                            || crate::commands::stress::running(&self.app)
                            || matches!(self.app.session.status, Status::Starting | Status::Generating { .. }));
                    if active {
                        self.app.view.pulse_frame = self.app.view.pulse_frame.wrapping_add(1);
                        self.app.view.loading.tick();
                    }
                    let switching = self.app.agents.switching_indicator;
                    if switching {
                        self.app.agents.frame = self.app.agents.frame.wrapping_add(1);
                    }
                    state.redraw_pending |= active || switching;
                }
                _ = blink.tick(), if !replaying
                    && self.app.view.app_focus =>
                {
                    self.app.view.cursor_on = !self.app.view.cursor_on;
                    state.redraw_pending = true;
                }
                _ = voice_tick.tick() => {
                    if self.app.recording_active() {
                        self.app.voice.frame = self.app.voice.frame.wrapping_add(1);
                        state.redraw_pending = true;
                    }
                }
                // Replay freezes the frame so a settled capture never catches the row mid-animation.
                _ = narrator_anim.tick(), if !replaying
                    && self.app.narrator.state != turn_summary::NarratorState::Idle =>
                {
                    self.app.narrator.frame = self.app.narrator.frame.wrapping_add(1);
                    state.redraw_pending = true;
                }
                Some(ev) = sources.voice.recv() => {
                    self.app.apply_voice_event(ev);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                // Draws nothing and settles nothing: analytics never alter the UI.
                Some(event) = sources.telemetry.recv() => {
                    if let Some(session_id) = self.app.session.session_id.clone() {
                        crate::telemetry::send(&self.client, &session_id, event);
                    }
                }
                Some(event) = sources.narrator.recv() => {
                    turn_summary::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.ready.recv() => {
                    // The missing-key verdict is a typed loop exit (the
                    // wizard is a pre-session surface, Python `run_onboarding`
                    // parity): the loop unwinds and the entrypoint reruns the
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
                        // the user has not seen yet.
                        while input.try_recv().is_ok() {}
                    }
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                changed = sources.files.changed(), if file_watch_open => {
                    if changed.is_ok() {
                        completion_manager::refresh(&mut self.app);
                        state.redraw_pending = true;
                        state.real_event_pending = true;
                    } else {
                        file_watch_open = false;
                    }
                }
                Some(notification) = sources.notifications.recv(), if deferred_notification.is_none() => {
                    absorb_notification(
                        &mut self.app,
                        &self.client,
                        sources,
                        notification,
                        &mut deferred_notification,
                    );
                    if settle_busy {
                        state.idle_marker_emitted = false;
                    }
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(loaded) = sources.config.recv() => {
                    crate::config::apply_loaded(&mut self.app, loaded);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                _ = tokio::time::sleep_until(preview_deadline(&self.app)), if self.app.theme_picker.preview_at.is_some() => {
                    crate::theme_picker::preview(&mut self.app);
                    // The gate held the marker for the armed preview, so this
                    // counts as a real event that owes the marker a re-emit.
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                _ = tokio::time::sleep_until(typing_pause_deadline(&self.app)), if self.app.question_app.pending.is_some() => {
                    crate::question_app::show_pending(&mut self.app);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                _ = tokio::time::sleep_until(approval_pause_deadline(&self.app)), if crate::approval::can_wake(&self.app) => {
                    crate::approval::show_pending(&mut self.app);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                _ = tokio::time::sleep_until(feedback_deadline(&self.app)), if self.app.feedback.hide_at.is_some() => {
                    crate::feedback::hide_expired(&mut self.app);
                    state.redraw_pending = true;
                }
                _ = tokio::time::sleep_until(subagent_refresh_deadline(&self.app)), if self.app.subagents.refresh_at.is_some() => {
                    // Python's drain: one fetch per 50ms window, even while updates stream.
                    crate::subagents::start_refresh(&mut self.app, &self.client);
                    state.redraw_pending = true;
                }
                Some(event) = sources.subagents.recv() => {
                    self.app.commit_finished();
                    crate::subagents::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.older_history.recv() => {
                    self.app.commit_finished();
                    crate::older_history::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.theme.recv() => {
                    crate::theme_picker::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.model.recv() => {
                    crate::model_picker::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.log_level.recv() => {
                    crate::log_level_picker::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.thinking.recv() => {
                    crate::thinking_picker::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.agents.recv() => {
                    crate::agents::apply_event(&mut self.app, &self.client, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                _ = tokio::time::sleep_until(spinner_deadline(&self.app)), if self.app.agents.spinner_at.is_some() => {
                    crate::agents::show_switch_spinner(&mut self.app);
                    state.redraw_pending = true;
                }
                Some(event) = sources.resume.recv() => {
                    self.app.commit_finished();
                    crate::resume_picker::apply_event(&mut self.app, &self.client, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.queue.recv() => {
                    self.app.commit_finished();
                    if let Some(session) =
                        crate::message_queue::apply_event(&mut self.app, &self.client, event)
                    {
                        crate::feedback::maybe_show(&mut self.app, &self.client, Some(session));
                    }
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.approval.recv() => {
                    self.app.commit_finished();
                    crate::approval::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.feedback.recv() => {
                    crate::feedback::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.paste_image.recv() => {
                    self.app.commit_finished();
                    crate::paste_image::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.rewind.recv() => {
                    crate::rewind::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.mcp.recv() => {
                    crate::mcp::apply_event(&mut self.app, &self.client, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.mcp_oauth.recv() => {
                    crate::mcp_oauth::apply_event(&mut self.app, &self.client, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.connector_auth.recv() => {
                    crate::connector_auth::apply_event(&mut self.app, &self.client, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                _ = tokio::time::sleep_until(crate::mcp::refresh_deadline(&self.app)), if self.app.mcp.open => {
                    crate::mcp::background_refresh(&mut self.app, &self.client);
                }
                Some(command) = sources.commands.recv() => {
                    apply_command(&mut self.app, &self.client, command);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                event = input.recv() => match handle_input_event(
                    &mut self.app,
                    &self.client,
                    &self.config_tx,
                    event,
                    replaying,
                ) {
                    InputOutcome::Exit => return Ok(LoopExit::Quit),
                    outcome => {
                        if state.apply_input(outcome) {
                            blink.reset();
                        }
                    }
                },
                changed = self.crash_rx.changed(), if !self.app.server_closed => {
                    if surface_server_close(&mut self.app, changed.is_ok() && *self.crash_rx.borrow()) {
                        state.redraw_pending = true;
                        state.real_event_pending = true;
                    }
                }
            }
        }
    }
}
