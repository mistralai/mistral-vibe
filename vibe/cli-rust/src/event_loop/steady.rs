//! Steady-state event selection.

use anyhow::Result;

use crate::app::Status;
use crate::{completion_manager, event_handler, turn_summary, ui};

use super::helpers::{
    apply_command, approval_pause_deadline, draw_synchronized, emit_idle_marker, feedback_deadline,
    handle_input_event, preview_deadline, spinner_deadline, step_scroll, typing_pause_deadline,
    InputOutcome, LoopState,
};
use super::notifications::{absorb_notification, surface_server_close};
use super::suspend::suspend_once;
use super::{EventLoop, REDRAW_INTERVAL};

impl EventLoop {
    pub(super) async fn steady(&mut self) -> Result<()> {
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
        let mut mcp_refresh = tokio::time::interval_at(
            tokio::time::Instant::now() + crate::mcp::BACKGROUND_REFRESH_INTERVAL,
            crate::mcp::BACKGROUND_REFRESH_INTERVAL,
        );
        mcp_refresh.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
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
                _ = shutdown.wait() => return Ok(()),
                _ = selection_scroll.tick(), if crate::selection::is_auto_scrolling(&self.app) => {
                    crate::selection::auto_scroll(&mut self.app);
                    state.idle_marker_emitted = false;
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                _ = redraw_tick.tick(), if state.redraw_pending => {
                    let animating = step_scroll(&mut self.app, replaying);
                    draw_synchronized(&mut self.terminal, &mut self.app)?;
                    let indexing = *sources.files.borrow() == 0
                        && completion_manager::active_is_file(&self.app);
                    let settled = if settle_busy {
                        self.app.is_settled()
                    } else {
                        self.app.is_idle()
                    };
                    if replaying && state.real_event_pending && !state.marker_held && !indexing {
                        if !settled || crate::selection::is_auto_scrolling(&self.app) {
                            state.idle_marker_emitted = false;
                        } else if !state.idle_marker_emitted {
                            emit_idle_marker();
                            state.idle_marker_emitted = true;
                        }
                    }
                    state.redraw_pending = animating;
                    state.real_event_pending = false;
                    continue;
                }
                _ = frame.tick() => {
                    let changed = !replaying
                        && !self.app.session.startup_config.disable_welcome_banner_animation
                        && self.app.view.banner.tick(std::time::Instant::now());
                    state.redraw_pending |= changed;
                }
                _ = spin.tick() => {
                    let active = !settle_busy
                        && (self.app.view.command_loading
                            || self.app.compacting
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
                _ = blink.tick(), if !replaying && self.app.view.app_focus => {
                    self.app.view.cursor_on = !self.app.view.cursor_on;
                    state.redraw_pending = true;
                }
                _ = voice_tick.tick() => {
                    if self.app.recording_active() {
                        self.app.voice.frame = self.app.voice.frame.wrapping_add(1);
                        state.redraw_pending = true;
                    }
                }
                // Replay freezes the frame: a settled capture must not catch
                // the `summarizing` row mid-animation.
                _ = narrator_anim.tick(), if !replaying
                    && self.app.narrator.state == turn_summary::NarratorState::Summarizing =>
                {
                    self.app.narrator.frame = self.app.narrator.frame.wrapping_add(1);
                    state.redraw_pending = true;
                }
                Some(ev) = sources.voice.recv() => {
                    self.app.apply_voice_event(ev);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.narrator.recv() => {
                    turn_summary::apply_event(&mut self.app, event);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                Some(event) = sources.ready.recv() => {
                    if event_handler::apply_startup_event(&mut self.app, &self.client, &self.config_tx, event) {
                        return Ok(());
                    }
                    if self.app.trust.open {
                        // Keys typed at the chat frame must not answer a gate
                        // the user has not seen yet.
                        while input.rx.try_recv().is_ok() {}
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
                    self.app.config_screen.fields = loaded.fields;
                    self.app.config_screen.targets = loaded.targets;
                    self.app.config_screen.selected = 0;
                    self.app.config_screen.scroll = 0;
                    self.app.config_screen.free_scroll = false;
                    self.app.config_screen.loading = false;
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
                _ = mcp_refresh.tick(), if self.app.mcp.open => {
                    crate::mcp::refresh(&mut self.app, &self.client);
                }
                Some(command) = sources.commands.recv() => {
                    apply_command(&mut self.app, &self.client, command);
                    state.redraw_pending = true;
                    state.real_event_pending = true;
                }
                event = input.rx.recv() => match handle_input_event(
                    &mut self.app,
                    &self.client,
                    &self.config_tx,
                    event,
                    replaying,
                ) {
                    InputOutcome::Exit => return Ok(()),
                    outcome => state.apply_input(outcome, &mut blink),
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
