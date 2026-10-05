//! Composer edit checkpoints and undo/redo.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::app::{App, ChatInput};
use crate::chat_input::Action;
use crate::edit_history::Snapshot;
use crate::{completion_manager, input, keymap};

pub fn before_key(app: &mut App, key: &KeyEvent) -> Option<(Snapshot, bool)> {
    let action = keymap::action_for(key);
    let edit = action.as_ref().is_some_and(Action::is_edit);
    let isolated = key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('x')
        || key.code == KeyCode::Tab
        || key.code == KeyCode::Enter && completion_manager::is_open(app);
    if edit || isolated {
        app.chat_input.normalize_positions();
        return Some((Snapshot::capture(&app.chat_input), isolated));
    }
    app.chat_input.edit_history.checkpoint();
    None
}

pub fn restore(app: &mut App, action: &Action) {
    let redo = *action == Action::Redo;
    if !app.chat_input.restore_edit(redo) {
        return;
    }
    input::reset_history_state(app);
    completion_manager::input_changed(app);
}

pub fn finish(input: &mut ChatInput, before: Option<(Snapshot, bool)>) {
    if let Some((snapshot, isolated)) = before {
        input.record_edit(snapshot, isolated, std::time::Instant::now());
    }
}
