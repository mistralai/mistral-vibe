//! Ctrl+Y yanks the last killed text back into the composer.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::app::App;
use crate::utils::input_edit;
use crate::{completion_manager, input, long_paste};

/// Ctrl+Y on Unix: copies a selection, else yanks the kill buffer into the composer.
pub fn is_yank_key(key: &KeyEvent) -> bool {
    cfg!(unix)
        && key.modifiers.contains(KeyModifiers::CONTROL)
        && !key.modifiers.contains(KeyModifiers::SHIFT)
        && key.code == KeyCode::Char('y')
}

/// Insert the last killed text at the caret as it was: its pastes collapse again, images stay attached.
pub fn yank(app: &mut App) {
    let text = app.chat_input.kill_buffer.clone();
    if text.is_empty() {
        return;
    }
    app.chat_input.scroll = None;
    app.chat_input.anchor = None;
    input::reset_history_state(app);
    let display = app.chat_input.collapsed_pastes.display(&text);
    let input = &mut app.chat_input;
    let start = input.cursor;
    input_edit::insert(&mut input.input, &mut input.cursor, &text);
    input.sync_mentions_at(start);
    for (from, to, chars) in crate::collapsed_pastes::ranges(&text, display.as_ref())
        .into_iter()
        .rev()
    {
        let label = long_paste::collapse_range(input, start + from, start + to, chars);
        input.cursor = input.cursor - (to - from) + label;
    }
    crate::image_placeholders::mark_known(input);
    long_paste::dismiss(app);
    completion_manager::input_changed(app);
}
