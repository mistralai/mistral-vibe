//! Input-event routing: terminal events in, one bounded outcome out, and
//! the key dispatch across every open surface.

use std::sync::Arc;

use crossterm::event::{Event, KeyCode, KeyEventKind, MouseEventKind};
use tokio::sync::mpsc;

use crate::app::App;
use crate::focus::Focus;
use crate::server::Client;
use crate::{config, input};

pub(crate) enum InputOutcome {
    Exit,
    Ignore,
    Redraw { reset_blink: bool },
}

/// Per-loop drawing state shared across the select arms (the wizard flow's, too).
pub(crate) struct LoopState {
    pub(crate) redraw_pending: bool,
    /// The input a pointer burst stopped at, handled before any newer input.
    pub(crate) deferred_input: Option<Event>,
}

impl LoopState {
    pub(crate) fn new() -> Self {
        Self {
            redraw_pending: false,
            deferred_input: None,
        }
    }

    /// Fold one input event's outcome into the loop state; the return says
    /// whether the caller owes its blink interval a reset (the wizard flow
    /// has no blinking cursor, so it ignores it).
    pub(crate) fn apply_input(&mut self, outcome: InputOutcome) -> bool {
        match outcome {
            // Exit is handled by the caller before folding.
            InputOutcome::Exit | InputOutcome::Ignore => false,
            InputOutcome::Redraw { reset_blink } => {
                self.redraw_pending = true;
                reset_blink
            }
        }
    }
}

/// Queued reports one pointer burst may fold into a single frame.
const MAX_POINTER_BATCH: usize = 64;

/// The next input: the one a pointer burst stopped at, else the terminal's next event.
pub(super) async fn next_input(
    input: &mut mpsc::Receiver<Event>,
    deferred: &mut Option<Event>,
) -> Option<Event> {
    match deferred.take() {
        Some(event) => Some(event),
        None => input.recv().await,
    }
}

/// Handle `event` and the pointer reports queued behind it in one frame; `None` exits, else the blink reset.
pub(super) fn handle_input_batch(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    input: &mut mpsc::Receiver<Event>,
    mut event: Option<Event>,
    state: &mut LoopState,
) -> Option<bool> {
    let mut reset_blink = false;
    let mut handled = 0;
    loop {
        let burst = event.as_ref().is_some_and(is_pointer_burst);
        let outcome = handle_input_event(app, client, config_tx, event);
        // Any input can start or end a gesture: cell reports are held exactly while one is captured.
        crate::terminal::hold_cell_reports(app.view.mouse.holds_target());
        match outcome {
            InputOutcome::Exit => return None,
            outcome => reset_blink |= state.apply_input(outcome),
        }
        handled += 1;
        if !burst || handled == MAX_POINTER_BATCH {
            return Some(reset_blink);
        }
        let Some(next) = burst_next(input, &mut state.deferred_input) else {
            return Some(reset_blink);
        };
        event = Some(next);
    }
}

/// The next queued pointer report of a burst; any other queued input is deferred instead.
fn burst_next(input: &mut mpsc::Receiver<Event>, deferred: &mut Option<Event>) -> Option<Event> {
    let next = input.try_recv().ok()?;
    if is_pointer_burst(&next) {
        return Some(next);
    }
    *deferred = Some(next);
    None
}

fn is_pointer_burst(event: &Event) -> bool {
    matches!(
        event,
        Event::Mouse(mouse) if matches!(
            mouse.kind,
            MouseEventKind::Moved
                | MouseEventKind::Drag(_)
                | MouseEventKind::ScrollUp
                | MouseEventKind::ScrollDown
                | MouseEventKind::ScrollLeft
                | MouseEventKind::ScrollRight
        )
    )
}

fn handle_input_event(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    event: Option<Event>,
) -> InputOutcome {
    if event.as_ref().is_some_and(crate::replay_fence::observe) {
        return InputOutcome::Redraw { reset_blink: false };
    }
    // A vim operator survives only a key the composer reads.
    let vim_pending = event
        .as_ref()
        .and_then(|event| crate::vim::take_pending(app, event));
    match event {
        Some(Event::Key(key)) if key.kind == KeyEventKind::Press => {
            if input::request_suspend(app, &key) {
                return InputOutcome::Redraw { reset_blink: false };
            }
            // Esc aborts a drag in flight; any other key ends it like a release.
            crate::mouse::cancel_capture(app, key.code != KeyCode::Esc);
            if dispatch_key(app, client, config_tx, key, vim_pending) {
                return InputOutcome::Exit;
            }
            app.set_app_focus_from_terminal(true);
            InputOutcome::Redraw { reset_blink: true }
        }
        Some(Event::Mouse(mouse)) => {
            let reset_blink = matches!(mouse.kind, MouseEventKind::Down(_));
            if reset_blink {
                app.set_app_focus_from_terminal(true);
            }
            crate::mouse::handle(app, client, config_tx, mouse);
            InputOutcome::Redraw { reset_blink }
        }
        Some(Event::Paste(text)) => {
            drop_box_selection(app);
            crate::ui::notice::answered(app);
            match app.focus() {
                // These surfaces take no text input and hide the composer; the paste is dropped.
                Focus::Trust
                | Focus::Approval
                | Focus::VoiceApp
                | Focus::BranchApp
                | Focus::ResumePicker
                | Focus::McpOAuth
                | Focus::ConnectorAuth
                | Focus::Rewind
                | Focus::ThemePicker
                | Focus::ModelPicker
                | Focus::LogLevelPicker
                | Focus::ThinkingPicker
                | Focus::ArtifactsModal
                | Focus::ContextApp => {}
                Focus::Question => crate::question_input::handle_paste(app, text),
                Focus::ProxySetup => crate::proxy_setup::paste(app, &text),
                Focus::Config => config::handle_paste(app, text),
                Focus::ReleaseNotes => crate::release_notes::paste(app, &text),
                Focus::Mcp => crate::mcp::search::paste(app, &text),
                Focus::Plugins => crate::plugins::paste(app, &text),
                Focus::Skills => crate::skills_browser::paste(app, client, &text),
                Focus::SubagentList | Focus::Composer => input::handle_paste(app, text),
            }
            InputOutcome::Redraw { reset_blink: false }
        }
        Some(Event::Resize(_, _)) => {
            let _ = crate::terminal::sync_pixel_reports();
            crate::config_edit::resized(app);
            InputOutcome::Redraw { reset_blink: false }
        }
        Some(Event::FocusGained) => {
            app.terminal_notifier.set_focus(true);
            app.set_app_focus_from_terminal(true);
            InputOutcome::Redraw { reset_blink: true }
        }
        Some(Event::FocusLost) => {
            app.terminal_notifier.set_focus(false);
            app.set_app_focus(false);
            crate::mouse::cancel_capture(app, true);
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
    vim_pending: Option<char>,
) -> bool {
    // Any key answers a refusal, whichever surface has focus.
    crate::ui::notice::answered(app);
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
        Focus::ReleaseNotes => crate::release_notes::handle_key(app, key),
        Focus::VoiceApp => crate::voice_app::handle_key(app, client, key),
        Focus::BranchApp => crate::branch_app::handle_key(app, key),
        Focus::ProxySetup => crate::proxy_setup::handle_key(app, client, key),
        Focus::ResumePicker => crate::resume_picker::handle_key(app, client, key),
        Focus::Mcp => input::handle_mcp_key(app, client, key),
        Focus::Plugins => crate::plugins::handle_key(app, client, key),
        Focus::Skills => crate::skills_browser::handle_key(app, client, key),
        Focus::ContextApp => crate::context_app::handle_key(app, key),
        Focus::McpOAuth => input::handle_mcp_oauth_key(app, client, key),
        Focus::ConnectorAuth => input::handle_connector_auth_key(app, client, key),
        Focus::Rewind => crate::rewind::handle_key(app, client, key),
        Focus::ThemePicker => input::handle_theme_key(app, client, key),
        Focus::ModelPicker => input::handle_model_key(app, client, key),
        Focus::LogLevelPicker => input::handle_log_level_key(app, client, key),
        Focus::ThinkingPicker => input::handle_thinking_key(app, client, key),
        Focus::ArtifactsModal => input::handle_artifacts_modal_key(app, key),
        Focus::SubagentList | Focus::Composer => {
            return crate::vim::route(app, key, vim_pending, |app, key| {
                input::handle_key(app, client, config_tx, key)
            });
        }
    }
    false
}

#[cfg(test)]
mod tests;
