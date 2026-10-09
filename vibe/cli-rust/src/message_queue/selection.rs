//! Queue selection and edit mode (ADR 0013), driven from the chat input.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::app::{App, Status};
use crate::hints::{action, key, Hint};
use crate::server::Client;
use crate::ui;

use super::ConsumedEdit;

const SELECTION_HINTS: &[Hint] = &[
    (key::NAV, action::SELECT),
    (key::ENTER, action::EDIT),
    (key::BACKSPACE_CTRL_C, action::REMOVE),
    (key::ESC, action::EXIT),
];
const EDIT_HINTS: &[Hint] = &[(key::ENTER, action::SAVE), (key::ESC, action::DISCARD)];
const CONSUMED_EDIT_HINTS: &[Hint] = &[
    (key::ENTER, action::SUBMIT_AS_NEW),
    (key::ESC, action::DISCARD),
];
const CONSUMED_EDIT_HINT: &str = "This message was already processed - press Enter again.";
const CONSUMED_EDIT_HINT_SECS: u64 = 8;
const REPLACEMENT_PENDING_HINT: &str = "Saving queued prompt…";
const REPLACEMENT_PENDING_HINT_SECS: u64 = 3;

/// Queue mode's key hints, derived from the state its keys dispatch on.
pub fn mode_hints(app: &App) -> Option<&'static [Hint]> {
    app.queue.selected.as_ref()?;
    Some(match app.queue.consumed_edit {
        Some(_) => CONSUMED_EDIT_HINTS,
        _ if app.queue.editing => EDIT_HINTS,
        _ => SELECTION_HINTS,
    })
}

/// Whether Up opens queue mode: startup or a turn is running, and prompts are queued.
pub fn is_available(app: &App) -> bool {
    matches!(
        app.session.status,
        Status::Starting | Status::Generating { .. }
    ) && !app.queue.is_empty()
        && !super::mutation_in_flight(app)
}

/// Selection mode owns the whole keyboard: ↑↓/jk move the highlight, Enter
/// edits, Backspace/Ctrl+C removes, Escape exits.
pub fn handle_selection_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if super::mutation_in_flight(app) {
        return;
    }
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    match key.code {
        KeyCode::Up => select_older(app),
        KeyCode::Down => select_newer(app),
        KeyCode::Char('k') if key.modifiers.is_empty() => select_older(app),
        KeyCode::Char('j') if key.modifiers.is_empty() => select_newer(app),
        KeyCode::Enter => edit_selected(app),
        KeyCode::Backspace => super::remove_selected(app, client),
        KeyCode::Char('c') if ctrl => super::remove_selected(app, client),
        KeyCode::Esc => exit(app),
        _ => {}
    }
}

/// Open queue mode on the newest queued prompt, saving the current draft.
pub fn enter(app: &mut App) -> bool {
    if !is_available(app) || app.queue.selected.is_some() {
        return false;
    }
    app.queue.draft = app.chat_input.submitted_text();
    app.queue.draft_snapshot = Some(crate::edit_history::Snapshot::capture(&app.chat_input));
    app.queue.editing = false;
    app.queue.consumed_edit = None;
    select(app, app.queue.len() - 1);
    true
}

/// Move the highlight one prompt towards the head of the queue.
pub fn select_older(app: &mut App) {
    let Some(position) = app.queue.selected_position() else {
        return;
    };
    if position > 0 {
        select(app, position - 1);
    }
}

/// Move the highlight one prompt towards the tail; past the newest, exit.
pub fn select_newer(app: &mut App) {
    let Some(position) = app.queue.selected_position() else {
        return;
    };
    if position + 1 < app.queue.len() {
        select(app, position + 1);
    } else {
        exit(app);
    }
}

/// Load the highlighted prompt into the input for editing.
pub fn edit_selected(app: &mut App) {
    let Some(position) = app.queue.selected_position() else {
        return;
    };
    let server_message_id = &app.queue.items[position].server_message_id;
    if app
        .queue
        .items
        .iter()
        .filter(|item| &item.server_message_id == server_message_id)
        .any(|item| item.replacing)
    {
        ui::notice::show(app, REPLACEMENT_PENDING_HINT, REPLACEMENT_PENDING_HINT_SECS);
        return;
    }
    app.queue.editing = true;
    app.queue.consumed_edit = None;
    let text = app.queue.items[position].raw_edit_text();
    crate::long_paste::load_collapsed_prompt(app, text);
    crate::composer_paths::rewrite_image_paths(&mut app.chat_input);
}

/// Leave edit mode back to selection, dropping any consumed-edit warning.
pub fn end_edit(app: &mut App) {
    let consumed_position = app.queue.consumed_edit.take().map(|edit| match edit {
        ConsumedEdit::AwaitingConfirmation { position } | ConsumedEdit::Confirmed { position } => {
            position
        }
    });
    if let Some(position) = consumed_position {
        ui::notice::clear(app);
        if app.queue.is_empty() {
            app.queue.editing = false;
            exit(app);
            return;
        }
        select(app, position.min(app.queue.len() - 1));
    }
    app.queue.editing = false;
    clear_input(app);
}

/// First Enter after promotion asks before copying the edit into a new prompt.
pub fn confirm_consumed_edit(app: &mut App) -> bool {
    let Some(ConsumedEdit::AwaitingConfirmation { position }) = app.queue.consumed_edit else {
        return false;
    };
    app.queue.consumed_edit = Some(ConsumedEdit::Confirmed { position });
    ui::notice::show_warning(app, CONSUMED_EDIT_HINT, CONSUMED_EDIT_HINT_SECS);
    true
}

/// Submit the confirmed copy-on-write edit, leaving queue mode entirely: the
/// draft comes back and the normal bindings resume for the enqueued copy.
pub fn finish_consumed_edit(app: &mut App) -> bool {
    if !matches!(
        app.queue.consumed_edit,
        Some(ConsumedEdit::Confirmed { .. })
    ) {
        return false;
    }
    app.queue.editing = false;
    ui::notice::clear(app);
    exit(app);
    crate::completion_manager::input_changed(app);
    true
}

/// Leave queue mode, restoring the draft the user had before it opened.
pub fn exit(app: &mut App) {
    let was_editing = app.queue.editing;
    app.queue.selected = None;
    app.queue.editing = false;
    app.queue.consumed_edit = None;
    let draft = std::mem::take(&mut app.queue.draft);
    let snapshot = app.queue.draft_snapshot.take();
    // Browsing leaves this draft in place; do not reset its mode or edit history.
    if app.chat_input.submitted_text() != draft {
        match snapshot {
            Some(snapshot) => snapshot.restore(&mut app.chat_input),
            None => crate::long_paste::load_collapsed(app, draft),
        }
    }
    app.chat_input.cursor = 0;
    app.chat_input.anchor = None;
    app.chat_input.scroll = None;
    if was_editing {
        clear_input(app);
    }
}

/// Re-point the highlight after the prompt at `index` left the queue: the queue
/// is FIFO, so the surviving neighbour is the one that took its position.
pub(super) fn reselect(app: &mut App, index: usize) {
    if app.queue.is_empty() {
        exit(app);
        return;
    }
    select(app, index.min(app.queue.len() - 1));
}

fn select(app: &mut App, position: usize) {
    app.queue.selected = Some(app.queue.items[position].message_id.clone());
}

fn clear_input(app: &mut App) {
    app.chat_input.clear();
    crate::completion_manager::input_changed(app);
}
