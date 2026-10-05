//! Input-event routing: terminal events in, one bounded outcome out, and
//! the key dispatch across every open surface.

use std::sync::Arc;

use crossterm::event::{Event, KeyEventKind, MouseEventKind};
use tokio::sync::mpsc;

use crate::app::App;
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
            if app.trust.open {
                // Nothing to paste into before the session exists.
            } else if app.approval.open {
                // The approval app takes no text input; the paste is dropped.
            } else if app.question_app.open {
                crate::question_input::handle_paste(app, text);
            } else if app.voice_app.open {
                // Voice settings take no text input; the paste is dropped.
            } else if app.config_screen.open {
                config::handle_paste(app, text);
            } else if app.vibe_code_project.open {
                crate::vibe_code_project::input::paste(app, &text);
            } else if app.mcp.open {
                crate::mcp::search::paste(app, &text);
            } else {
                input::handle_paste(app, text);
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

fn dispatch_key(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    key: crossterm::event::KeyEvent,
) -> bool {
    if app.trust.open {
        crate::trust_folders::handle_key(app, key)
    } else if let Some(exit) = input::handle_priority_key(app, client, key) {
        exit
    } else if key.code == crossterm::event::KeyCode::Esc
        && (app.approval.open || app.question_app.open)
        && app.todo_sidebar.close()
    {
        // A docked plan panel closes before Esc rejects the tool or question the turn waits on.
        false
    } else if app.approval.open {
        crate::approval::handle_key(app, client, key);
        false
    } else if app.question_app.open {
        crate::question_input::handle_key(app, client, key);
        false
    } else if app.config_screen.open {
        config::handle_key(app, client, config_tx, key);
        false
    } else if app.voice_app.open {
        crate::voice_app::handle_key(app, client, key);
        false
    } else if app.vibe_code_project.open
        || (app.vibe_code_project.pending && key.code == crossterm::event::KeyCode::Esc)
    {
        crate::vibe_code_project::input::handle_key(app, client, key);
        false
    } else if app.resume_picker.open {
        crate::resume_picker::handle_key(app, client, key);
        false
    } else if app.mcp.open {
        input::handle_mcp_key(app, client, key);
        false
    } else if app.mcp_oauth.open {
        input::handle_mcp_oauth_key(app, client, key);
        false
    } else if app.connector_auth.open {
        input::handle_connector_auth_key(app, client, key);
        false
    } else if app.rewind.open {
        crate::rewind::handle_key(app, client, key);
        false
    } else {
        input::handle_key(app, client, config_tx, key)
    }
}
