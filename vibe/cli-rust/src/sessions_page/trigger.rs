//! Left on an empty composer opens the Sessions page; anywhere else it moves the caret.

use crossterm::event::{KeyCode, KeyEvent};

use crate::app::App;
use crate::input_modes::InputMode;

/// Whether `key` opens the page instead of reaching the composer.
pub fn left_opens(app: &App, key: &KeyEvent) -> bool {
    key.code == KeyCode::Left
        && key.modifiers.is_empty()
        && crate::input::composer_reachable(app)
        && app.chat_input.mode == InputMode::Prompt
        && app.chat_input.full_text().is_empty()
        && !crate::completion_manager::is_open(app)
        && app.queue.selected.is_none()
        && !app.queue.editing
        && app.reverse_search.is_none()
        && !(app.vim.enabled && app.vim.mode == crate::vim::Mode::Normal)
}
