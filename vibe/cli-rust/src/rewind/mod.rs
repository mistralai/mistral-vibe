//! Rewind mode: pick an earlier user message, then rewind the session to it.
//! Mirrors Python's `App.action_rewind_prev` and its two-step `RewindApp`.

mod request;

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::{KeyCode, KeyEvent};

use crate::app::{App, Status, ToastSeverity};
use crate::collapsed_pastes::Collapsed;
use crate::commands::submission::new_message_id;
use crate::input_modes::ClassifiedInput;
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
        preview: Collapsed,
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
    app.rewind.preview = Collapsed::default();
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
        KeyCode::Char('q') => quit(app),
        _ => {
            if let Some(option) = crate::list_nav::digit(&key, options(app).len()) {
                choose(app, client, option);
            }
        }
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
    app.rewind.selected = crate::list_nav::wrap(app.rewind.selected, count, down);
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
fn current_index(app: &App, messages: &[(usize, String, Collapsed)]) -> Option<usize> {
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
    // Read before the transcript is rebuilt: the rewound message's collapsed
    // pastes and attached images.
    let rewound = app
        .rewind
        .entry_id
        .as_deref()
        .and_then(|id| app.view.transcript.entry_raw(id));
    let display = rewound
        .and_then(|raw| raw.get("userDisplayContent"))
        .cloned();
    let images = rewound
        .map(crate::inline_images::attachments)
        .unwrap_or_default();
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
    crate::worktree::track_state(app, &state);
    app.expand_rebuilt_tools();
    if !inplace {
        local::add_rewind_fork(
            &mut app.view.transcript,
            &new_message_id(),
            &old_session_id,
            &new_session_id,
        );
    }
    // The rewound message goes back into the composer, caret at the end, ready to be edited.
    // A skill invocation reloads in `/` mode as typed; any other message is a prompt.
    match crate::input_modes::classify(&message, &app.completion.skills) {
        ClassifiedInput::Skill { .. } => app.chat_input.load_full_text(message.clone()),
        _ => app.chat_input.load_prompt_text(message.clone()),
    }
    crate::long_paste::restore(app, display.as_ref());
    crate::inline_images::restore_placeholders(app, &message, &images);
    app.chat_input.cursor = app.chat_input.input.len();
    crate::completion_manager::input_changed(app);
    // A trailing `@file` or `/skill` mention stays closed until the next edit, like a recall.
    if crate::completion_manager::active_is_mention(app) {
        crate::completion_manager::reset_for_recall(app);
    }
    app.view.scroll = 0;
    app.view.scroll_target = 0;
}

#[cfg(test)]
mod tests;
