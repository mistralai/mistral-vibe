//! Ctrl+S stashes the composer draft; the next submit gives it back.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::app::App;
use crate::edit_history::{EditHistory, Snapshot};
use crate::{completion_manager, input, long_paste};

/// A draft set aside with its undo history.
pub struct Stash {
    draft: Snapshot,
    edit_history: EditHistory,
}

pub fn is_stash_key(key: &KeyEvent) -> bool {
    key.modifiers == KeyModifiers::CONTROL && key.code == KeyCode::Char('s')
}

/// Whether Ctrl+S reaches the stash: reverse search, recording, the queue and the subagent
/// views each take it first, and the restore hint must not advertise a dead key.
pub fn key_reaches_stash(app: &App) -> bool {
    app.reverse_search.is_none()
        && !app.recording_active()
        && app.queue.selected.is_none()
        && !app.queue.editing
        && !input::composer_hidden(app)
}

/// Swap the draft with the stash: a typed draft is set aside, the stashed one comes back.
pub fn toggle(app: &mut App) {
    app.chat_input.normalize_positions();
    // From a recalled history entry, act on the typed draft Down would give back, as Ctrl+R does;
    // with nothing typed before Up, the recalled entry itself is stashed.
    let typed = app.chat_input.recall_draft.take();
    input::reset_history_state(app);
    if let Some(typed) = typed.filter(|typed| !typed.is_blank()) {
        typed.restore(&mut app.chat_input);
    }
    let input = &mut app.chat_input;
    let draft = (!input.full_text().trim().is_empty()).then(|| Stash {
        draft: Snapshot::capture(input),
        edit_history: std::mem::take(&mut input.edit_history),
    });
    let stashing = draft.is_some();
    let previous = std::mem::replace(&mut input.stash, draft);
    if stashing || previous.is_some() {
        restore(app, previous);
    }
}

/// Give the stashed draft back once a submit has emptied the composer.
pub fn pop_after_submit(app: &mut App) {
    if !app.chat_input.full_text().is_empty() {
        return;
    }
    if let Some(stash) = app.chat_input.stash.take() {
        restore(app, Some(stash));
    }
}

/// Put `stash` in the composer, or empty it.
fn restore(app: &mut App, stash: Option<Stash>) {
    long_paste::dismiss(app);
    input::reset_history_state(app);
    match stash {
        Some(stash) => {
            stash.draft.restore(&mut app.chat_input);
            app.chat_input.edit_history = stash.edit_history;
        }
        None => app.chat_input.clear(),
    }
    completion_manager::input_changed(app);
}
