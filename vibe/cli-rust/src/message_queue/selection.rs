//! Queue selection and edit mode (ADR 0013), driven from the chat input.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent};

use crate::app::{App, Status};
use crate::server::Client;
use crate::ui;

use super::ConsumedEdit;

/// Shown when queue mode opens (Python `_try_enter_queue_selection`).
const SELECTION_HINT: &str =
    "Up/Down: select  ·  Enter: edit  ·  Backspace/Delete: remove  ·  Esc: exit";
const SELECTION_HINT_SECS: u64 = 3;
/// Shown while editing a queued prompt; stays up until the edit settles.
const EDIT_HINT: &str = "Enter to save · Esc to discard";
const CONSUMED_EDIT_HINT: &str =
    "This message was already processed - press Enter to submit as new, or Escape to discard.";
const CONSUMED_EDIT_HINT_SECS: u64 = 8;
const REPLACEMENT_PENDING_HINT: &str = "Saving queued prompt…";

/// Whether Up opens queue mode: startup or a turn is running, and prompts are queued.
pub fn is_available(app: &App) -> bool {
    matches!(
        app.session.status,
        Status::Starting | Status::Generating { .. }
    ) && !app.queue.is_empty()
        && !super::mutation_in_flight(app)
}

/// Selection mode owns the whole keyboard: Up/Down move the highlight, Enter
/// edits, Backspace/Delete removes, Escape exits.
pub fn handle_selection_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if super::mutation_in_flight(app) {
        return;
    }
    match key.code {
        KeyCode::Up => select_older(app),
        KeyCode::Down => select_newer(app),
        KeyCode::Enter => edit_selected(app),
        KeyCode::Backspace | KeyCode::Delete => super::remove_selected(app, client),
        KeyCode::Esc => exit(app),
        _ => {}
    }
}

/// Open queue mode on the newest queued prompt, saving the current draft.
pub fn enter(app: &mut App) -> bool {
    if !is_available(app) || app.queue.selected.is_some() {
        return false;
    }
    app.queue.draft = app.chat_input.full_text();
    app.queue.editing = false;
    app.queue.consumed_edit = None;
    select(app, app.queue.len() - 1);
    ui::notice::show(app, SELECTION_HINT, SELECTION_HINT_SECS);
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
        ui::notice::show(app, REPLACEMENT_PENDING_HINT, SELECTION_HINT_SECS);
        return;
    }
    app.queue.editing = true;
    app.queue.consumed_edit = None;
    app.chat_input
        .load_full_text(app.queue.items[position].edit_text());
    ui::notice::pin(app, EDIT_HINT);
}

/// Leave edit mode back to selection and restore its controls hint.
pub fn end_edit(app: &mut App) {
    let consumed_position = app.queue.consumed_edit.take().map(|edit| match edit {
        ConsumedEdit::AwaitingConfirmation { position } | ConsumedEdit::Confirmed { position } => {
            position
        }
    });
    if let Some(position) = consumed_position {
        if app.queue.is_empty() {
            app.queue.editing = false;
            exit(app);
            ui::notice::clear(app);
            return;
        }
        select(app, position.min(app.queue.len() - 1));
    }
    app.queue.editing = false;
    clear_input(app);
    ui::notice::show(app, SELECTION_HINT, SELECTION_HINT_SECS);
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

/// Re-snapshot queue selection before the confirmed copy-on-write enqueue.
pub fn finish_consumed_edit(app: &mut App) -> bool {
    if !matches!(
        app.queue.consumed_edit,
        Some(ConsumedEdit::Confirmed { .. })
    ) {
        return false;
    }
    end_edit(app);
    true
}

/// Leave queue mode, restoring the draft the user had before it opened. Only an
/// edit clears the notice; a plain selection hint runs out on its own timeout.
pub fn exit(app: &mut App) {
    let was_editing = app.queue.editing;
    app.queue.selected = None;
    app.queue.editing = false;
    app.queue.consumed_edit = None;
    // Python reloads the draft with `load_text`, which parks the caret at the start.
    app.chat_input
        .load_full_text(std::mem::take(&mut app.queue.draft));
    app.chat_input.cursor = 0;
    if was_editing {
        clear_input(app);
        ui::notice::clear(app);
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
