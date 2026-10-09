//! Input-event routing: terminal events in, one bounded outcome out, and
//! the key dispatch across every open surface.

use std::sync::Arc;

use crossterm::event::{Event, KeyCode, KeyEventKind, MouseEventKind};
use tokio::sync::mpsc;

use crate::app::App;
use crate::focus::Focus;
use crate::server::Client;
use crate::{config, input};

use super::{HOLD_KEY, RELEASE_KEY};

pub(crate) enum InputOutcome {
    Exit,
    Ignore,
    Marker(bool),
    Activity { reset_blink: bool },
    Redraw { reset_blink: bool },
}

/// Per-loop drawing and replay-marker state shared across the select arms
/// (the wizard flow's, too: one marker protocol, one state shape).
pub(crate) struct LoopState {
    pub(crate) redraw_pending: bool,
    pub(crate) real_event_pending: bool,
    pub(crate) idle_marker_emitted: bool,
    pub(crate) marker_held: bool,
}

impl LoopState {
    pub(crate) fn new() -> Self {
        Self {
            redraw_pending: false,
            real_event_pending: false,
            idle_marker_emitted: false,
            marker_held: false,
        }
    }

    /// Fold one input event's outcome into the loop state; the return says
    /// whether the caller owes its blink interval a reset (the wizard flow
    /// has no blinking cursor, so it ignores it).
    pub(crate) fn apply_input(&mut self, outcome: InputOutcome) -> bool {
        match outcome {
            // Exit is handled by the caller before folding.
            InputOutcome::Exit => false,
            InputOutcome::Ignore => false,
            InputOutcome::Marker(held) => {
                self.marker_held = held;
                if !held {
                    self.redraw_pending = true;
                    self.real_event_pending = true;
                }
                false
            }
            InputOutcome::Activity { reset_blink } => {
                self.idle_marker_emitted = false;
                self.redraw_pending = true;
                self.real_event_pending = true;
                reset_blink
            }
            InputOutcome::Redraw { reset_blink } => {
                self.redraw_pending = true;
                reset_blink
            }
        }
    }
}

pub(super) fn handle_input_event(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    event: Option<Event>,
    replaying: bool,
) -> InputOutcome {
    match event {
        Some(Event::Key(key))
            if replaying
                && matches!(
                    key.code,
                    crossterm::event::KeyCode::F(HOLD_KEY | RELEASE_KEY)
                ) =>
        {
            InputOutcome::Marker(key.code == crossterm::event::KeyCode::F(HOLD_KEY))
        }
        Some(Event::Key(key)) if key.kind == KeyEventKind::Press => {
            if input::request_suspend(app, &key) {
                return InputOutcome::Activity { reset_blink: false };
            }
            crate::mouse::cancel_capture(app);
            if dispatch_key(app, client, config_tx, key) {
                return InputOutcome::Exit;
            }
            app.set_app_focus_from_terminal(true);
            InputOutcome::Activity { reset_blink: true }
        }
        Some(Event::Mouse(mouse)) => {
            let reset_blink = matches!(mouse.kind, MouseEventKind::Down(_));
            if reset_blink {
                app.set_app_focus_from_terminal(true);
            }
            crate::mouse::handle(app, client, config_tx, mouse);
            InputOutcome::Activity { reset_blink }
        }
        Some(Event::Paste(text)) => {
            drop_box_selection(app);
            match app.focus() {
                // These surfaces take no text input; the paste is dropped.
                Focus::Trust | Focus::Approval | Focus::VoiceApp => {}
                Focus::Question => crate::question_input::handle_paste(app, text),
                Focus::ProxySetup => crate::proxy_setup::paste(app, &text),
                Focus::Config => config::handle_paste(app, text),
                Focus::VibeCodeProject => crate::vibe_code_project::input::paste(app, &text),
                Focus::Mcp => crate::mcp::search::paste(app, &text),
                Focus::Plugins => crate::plugins::paste(app, &text),
                Focus::ResumePicker
                | Focus::McpOAuth
                | Focus::ConnectorAuth
                | Focus::Rewind
                | Focus::ThemePicker
                | Focus::ModelPicker
                | Focus::LogLevelPicker
                | Focus::ThinkingPicker
                | Focus::SubagentList
                | Focus::Composer => input::handle_paste(app, text),
            }
            InputOutcome::Activity { reset_blink: false }
        }
        Some(Event::Resize(_, _)) => {
            crate::config_edit::resized(app);
            InputOutcome::Activity { reset_blink: false }
        }
        Some(Event::FocusGained) => {
            app.terminal_notifier.set_focus(true);
            app.set_app_focus_from_terminal(true);
            InputOutcome::Redraw { reset_blink: true }
        }
        Some(Event::FocusLost) => {
            app.terminal_notifier.set_focus(false);
            app.set_app_focus(false);
            crate::mouse::cancel_capture(app);
            app.view.mouse_position = None;
            InputOutcome::Redraw { reset_blink: false }
        }
        Some(_) => InputOutcome::Ignore,
        None => InputOutcome::Exit,
    }
}

/// Input a bottom app consumes can rewrite its box under a screen-anchored selection.
fn drop_box_selection(app: &mut App) {
    if app.view.bottom_app.is_some() {
        crate::selection::clear_region(app, crate::selection::RegionId::BottomApp);
    }
}

fn dispatch_key(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    key: crossterm::event::KeyEvent,
) -> bool {
    if app.focus() == Focus::Trust {
        return crate::trust_folders::handle_key(app, key);
    }
    if let Some(exit) = input::handle_priority_key(app, client, key) {
        return exit;
    }
    drop_box_selection(app);
    let esc = key.code == KeyCode::Esc;
    match app.focus() {
        // Handled above, before the priority keys.
        Focus::Trust => {}
        // A docked plan panel closes before Esc rejects the tool or question the turn waits on.
        Focus::Approval | Focus::Question if esc && app.todo_sidebar.close() => {}
        Focus::Approval => crate::approval::handle_key(app, client, key),
        Focus::Question => crate::question_input::handle_key(app, client, key),
        Focus::Config => config::handle_key(app, client, config_tx, key),
        Focus::VoiceApp => crate::voice_app::handle_key(app, client, key),
        Focus::ProxySetup => crate::proxy_setup::handle_key(app, client, key),
        Focus::VibeCodeProject => crate::vibe_code_project::input::handle_key(app, client, key),
        Focus::ResumePicker => crate::resume_picker::handle_key(app, client, key),
        Focus::Mcp => input::handle_mcp_key(app, client, key),
        Focus::Plugins => crate::plugins::handle_key(app, client, key),
        Focus::McpOAuth => input::handle_mcp_oauth_key(app, client, key),
        Focus::ConnectorAuth => input::handle_connector_auth_key(app, client, key),
        Focus::Rewind => crate::rewind::handle_key(app, client, key),
        Focus::ThemePicker => input::handle_theme_key(app, client, key),
        Focus::ModelPicker => input::handle_model_key(app, client, key),
        Focus::LogLevelPicker => input::handle_log_level_key(app, client, key),
        Focus::ThinkingPicker => input::handle_thinking_key(app, client, key),
        // Esc cancels a project request in flight before the composer sees it.
        Focus::SubagentList | Focus::Composer if esc && app.vibe_code_project.pending => {
            crate::vibe_code_project::input::handle_key(app, client, key)
        }
        Focus::SubagentList | Focus::Composer => {
            return input::handle_key(app, client, config_tx, key)
        }
    }
    false
}
