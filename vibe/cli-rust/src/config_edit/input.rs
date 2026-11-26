//! Config draft editing: the chat input keymap and actions, minus selection.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use super::{is_multiline, ConfigEdit};
use crate::chat_input::{apply, Action};
use crate::keymap;

pub(super) fn handle_key(edit: &mut ConfigEdit, key: KeyEvent) {
    let multiline = is_multiline(&edit.field);
    if multiline && matches!(key.code, KeyCode::Up | KeyCode::Down) {
        edit.cursor = crate::ui::config_edit::vertical_cursor(edit, key.code == KeyCode::Down);
        return;
    }
    let control = key.modifiers.contains(KeyModifiers::CONTROL);
    let action = match key.code {
        // Ctrl+Home/End move to the draft bounds; the chat input has no such binding.
        KeyCode::Home if control => {
            edit.cursor = 0;
            return;
        }
        KeyCode::End if control => {
            edit.cursor = edit.draft.len();
            return;
        }
        KeyCode::Enter if multiline => Action::Insert('\n'),
        _ => match keymap::action_for(&key) {
            Some(action) => action,
            None => return,
        },
    };
    // A single-line field never takes a newline (Ctrl+J, Shift+Enter).
    if !multiline && action == Action::Insert('\n') {
        return;
    }
    // The config editor draws no selection, so the anchor is discarded.
    let mut anchor = None;
    apply(&action, &mut edit.draft, &mut edit.cursor, &mut anchor);
    if action.is_edit() {
        edit.error = None;
    }
}
