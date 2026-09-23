//! Rewind mode: pick an earlier user message, then rewind the session to it.
//! Mirrors Python's `App.action_rewind_prev` and its two-step `RewindApp`.

mod request;

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::{KeyCode, KeyEvent};

use crate::app::{App, Status, ToastSeverity};
use crate::commands::submission::new_message_id;
use crate::server::{Client, PublicSessionState};
use crate::transcript::local;
use request::{confirm, select};

/// Window in which a second Escape enters rewind mode (Python `DOUBLE_ESC_DELAY`).
pub const DOUBLE_ESC_DELAY: Duration = Duration::from_millis(200);

/// Seconds a rewind toast stays up (Python `App.NOTIFICATION_TIMEOUT`).
const TOAST_SECS: u64 = 5;

/// The two option sets the panel walks through (Python `_RewindStep`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Step {
    #[default]
    Action,
    Persistence,
}

/// A server answer for the rewind flow, applied on the main thread.
pub enum Event {
    /// `session/rewind/read` answered for the message being highlighted.
    Selected {
        entry_index: usize,
        entry_id: String,
        preview: String,
        has_file_changes: bool,
    },
    /// `session/rewind` answered: the session now starts before the message.
    Done {
        message: String,
        restore_errors: Vec<String>,
        old_session_id: String,
        inplace: bool,
        state: Box<PublicSessionState>,
    },
    Failed(String),
}

/// `/rewind`, or Escape twice on an empty input: highlight the last user message.
pub fn start(app: &mut App, client: &Arc<Client>) {
    prev(app, client);
}

/// Move the highlight to the previous user message, entering rewind mode when
/// none is highlighted yet (Python `action_rewind_prev`).
pub fn prev(app: &mut App, client: &Arc<Client>) {
    if matches!(app.session.status, Status::Generating { .. }) {
        return;
    }
    let messages = app.view.transcript.user_messages();
    if messages.is_empty() {
        return;
    }
    let index = match current_index(app, &messages) {
        None => messages.len() - 1,
        // Above the first message there is nothing to select, so Python scrolls
        // the transcript home instead.
        Some(0) => {
            app.view.scroll_target = u16::MAX;
            app.view.scroll = u16::MAX;
            return;
        }
        Some(index) => index - 1,
    };
    select(app, client, &messages, index);
}

/// Move the highlight to the next user message (Python `action_rewind_next`).
pub fn next(app: &mut App, client: &Arc<Client>) {
    if !app.rewind.open {
        return;
    }
    let messages = app.view.transcript.user_messages();
    let Some(index) = current_index(app, &messages) else {
        return;
    };
    if index + 1 >= messages.len() {
        return;
    }
    select(app, client, &messages, index + 1);
}

/// Leave rewind mode, restoring the input box (Python `_exit_rewind_mode`).
pub fn quit(app: &mut App) {
    app.rewind.open = false;
    app.rewind.entry_id = None;
    app.rewind.preview.clear();
    app.rewind.step = Step::Action;
    app.rewind.restore_files = false;
    app.rewind.selected = 0;
}

/// The panel owns keys while open (Python `RewindApp.BINDINGS`).
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        // Escape backs out of the persistence step, else it walks further back.
        KeyCode::Esc if app.rewind.step == Step::Persistence => reset_to_action(app),
        KeyCode::Esc | KeyCode::Left => prev(app, client),
        KeyCode::Right => next(app, client),
        KeyCode::Up | KeyCode::Char('k') => navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => navigate(app, true),
        KeyCode::Enter => choose(app, client, app.rewind.selected),
        KeyCode::Char('1') => choose(app, client, 0),
        KeyCode::Char('2') => choose(app, client, 1),
        KeyCode::Char('q') => quit(app),
        _ => {}
    }
}

/// Labels of the current step, in order (Python `_build_*_options`).
pub fn options(app: &App) -> Vec<&'static str> {
    match app.rewind.step {
        Step::Action if app.rewind.has_file_changes => vec![
            "Edit & restore files to this point",
            "Edit without restoring files",
        ],
        Step::Action => vec!["Edit message from here"],
        Step::Persistence => vec!["Keep in current session", "Fork to a new session"],
    }
}

/// Move the highlight one option up/down, wrapping (Python `action_move_*`).
fn navigate(app: &mut App, down: bool) {
    let count = options(app).len();
    if count == 0 {
        return;
    }
    let step = if down { 1 } else { count - 1 };
    app.rewind.selected = (app.rewind.selected + step) % count;
}

/// Act on an option: the action step arms the restore choice and advances, the
/// persistence step confirms the rewind (Python `_handle_selection`).
fn choose(app: &mut App, client: &Arc<Client>, option: usize) {
    if option >= options(app).len() {
        return;
    }
    app.rewind.selected = option;
    match app.rewind.step {
        Step::Action => {
            app.rewind.restore_files = app.rewind.has_file_changes && option == 0;
            app.rewind.step = Step::Persistence;
            app.rewind.selected = 0;
        }
        Step::Persistence => confirm(app, client, option == 0),
    }
}

fn reset_to_action(app: &mut App) {
    app.rewind.step = Step::Action;
    app.rewind.restore_files = false;
    app.rewind.selected = 0;
}

/// Index of the highlighted message in the rewindable list.
fn current_index(app: &App, messages: &[(usize, String, String)]) -> Option<usize> {
    let entry_id = app.rewind.entry_id.as_deref()?;
    messages.iter().position(|(_, id, _)| id == entry_id)
}

/// Apply a server answer on the main thread and release the commit it held.
pub fn apply_event(app: &mut App, event: Event) {
    match event {
        Event::Selected {
            entry_index,
            entry_id,
            preview,
            has_file_changes,
        } => {
            // A new message resets the flow, like remounting the Python panel.
            app.rewind.open = true;
            app.rewind.entry_id = Some(entry_id);
            app.rewind.preview = preview;
            app.rewind.has_file_changes = has_file_changes;
            reset_to_action(app);
            app.view.scroll_to_entry = Some(entry_index);
        }
        Event::Done {
            message,
            restore_errors,
            old_session_id,
            inplace,
            state,
        } => apply_done(
            app,
            message,
            restore_errors,
            old_session_id,
            inplace,
            *state,
        ),
        Event::Failed(message) => {
            app.show_toast(message, ToastSeverity::Error, TOAST_SECS);
            if app.rewind.entry_id.is_none() {
                quit(app);
            }
        }
    }
    app.commit_finished();
}

fn apply_done(
    app: &mut App,
    message: String,
    restore_errors: Vec<String>,
    old_session_id: String,
    inplace: bool,
    state: PublicSessionState,
) {
    for error in restore_errors {
        app.show_toast(error, ToastSeverity::Warning, TOAST_SECS);
    }
    quit(app);
    let new_session_id = state.session.id.clone();
    app.set_session_id(new_session_id.clone());
    app.session.active_turn_id = None;
    // Python drops the queued prompts and rebuilds every widget from the
    // returned history, so no client-owned entry survives the rewind.
    app.view.transcript.clear();
    // Either rewind mode leaves the harness session without its todo list.
    app.todo_tracker.clear();
    app.queue.clear();
    app.view.expanded.clear();
    app.view.transcript.load_snapshot(&state);
    app.expand_rebuilt_tools();
    if !inplace {
        local::add_rewind_fork(
            &mut app.view.transcript,
            &new_message_id(),
            &old_session_id,
            &new_session_id,
        );
    }
    // The rewound message goes back into the composer, ready to be edited. The
    // Textual `value` setter parks the caret at the start, so this does too.
    app.chat_input.load_full_text(message);
    app.chat_input.cursor = 0;
    crate::completion_manager::input_changed(app);
    app.view.scroll = 0;
    app.view.scroll_target = 0;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn selected(app: &mut App, has_file_changes: bool) {
        apply_event(
            app,
            Event::Selected {
                entry_index: 0,
                entry_id: "entry-1".into(),
                preview: "fix the parser".into(),
                has_file_changes,
            },
        );
    }

    #[test]
    fn the_action_step_offers_a_restore_only_when_files_changed() {
        let mut app = App::default();
        selected(&mut app, true);
        assert_eq!(options(&app).len(), 2);
        selected(&mut app, false);
        assert_eq!(options(&app), ["Edit message from here"]);
    }

    #[test]
    fn navigation_wraps_around_the_current_step() {
        let mut app = App::default();
        selected(&mut app, true);
        navigate(&mut app, true);
        assert_eq!(app.rewind.selected, 1);
        navigate(&mut app, true);
        assert_eq!(app.rewind.selected, 0);
        navigate(&mut app, false);
        assert_eq!(app.rewind.selected, 1);
    }

    #[test]
    fn a_new_selection_reopens_on_the_action_step() {
        let mut app = App::default();
        selected(&mut app, true);
        app.rewind.step = Step::Persistence;
        app.rewind.restore_files = true;
        app.rewind.selected = 1;
        selected(&mut app, true);
        assert!(app.rewind.open);
        assert_eq!(app.rewind.step, Step::Action);
        assert!(!app.rewind.restore_files);
        assert_eq!(app.rewind.selected, 0);
        assert_eq!(app.view.scroll_to_entry, Some(0));
    }

    #[test]
    fn a_failed_read_leaves_rewind_mode_when_nothing_is_highlighted() {
        let mut app = App::default();
        apply_event(&mut app, Event::Failed("boom".into()));
        assert!(!app.rewind.open);
        assert!(!app.overlays.toasts.is_empty());
    }

    #[test]
    fn a_done_rewind_restores_the_message_into_the_composer() {
        let mut app = App::default();
        selected(&mut app, false);
        let state = serde_json::json!({
            "eventId": 3,
            "session": {"id": "new-session-id"},
            "history": [],
        });
        apply_event(
            &mut app,
            Event::Done {
                message: "fix the parser".into(),
                restore_errors: vec!["Failed to restore file: a.py".into()],
                old_session_id: "old-session-id".into(),
                inplace: false,
                state: Box::new(serde_json::from_value(state).expect("state")),
            },
        );
        assert!(!app.rewind.open);
        assert_eq!(app.chat_input.input, "fix the parser");
        assert_eq!(app.chat_input.cursor, 0);
        assert_eq!(app.session.session_id.as_deref(), Some("new-session-id"));
        assert!(!app.overlays.toasts.is_empty());
        // The fork notice is the only entry left after the history was replaced.
        assert_eq!(app.view.transcript.lines().count(), 1);
    }
}
