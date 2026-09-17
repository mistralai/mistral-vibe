//! Keyboard and mouse input for the `ask_user_question` bottom-app.

use std::sync::Arc;

use crate::server::Client;
use crossterm::event::{KeyCode, KeyEvent, MouseButton, MouseEvent, MouseEventKind};

use crate::app::App;
use crate::mouse::MOUSE_SCROLL_STEP;
use crate::question_app::{
    cancel, current_question, is_other_selected, is_within_grace_period, move_down, move_up,
    navigate_to_option, next_question, other_option_idx, other_text, prev_question, select,
    select_option, set_selected_option, submit_option_idx, submit_other, toggle_selection,
};
use crate::utils::input_edit;

/// The question app owns every key while it is open (Python `QuestionApp.on_key`).
/// The free-text row is focused exactly when the cursor sits on it, and then it
/// takes the printable keys, Backspace and the horizontal arrows.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let other_focused = is_other_selected(app);
    if !other_focused && handle_number_key(app, client, key) {
        return;
    }
    match key.code {
        KeyCode::Esc => cancel(app, client),
        KeyCode::Up => move_up(app),
        KeyCode::Down => move_down(app),
        KeyCode::Char('k') if !other_focused => move_up(app),
        KeyCode::Char('j') if !other_focused => move_down(app),
        KeyCode::Enter if other_focused => submit_other(app, client),
        KeyCode::Enter => select(app, client),
        KeyCode::Left if other_focused => move_caret(app, false),
        KeyCode::Right if other_focused => move_caret(app, true),
        KeyCode::Left if app.question_app.questions.len() > 1 => prev_question(app),
        KeyCode::Right if app.question_app.questions.len() > 1 => next_question(app),
        KeyCode::Backspace if other_focused => edit_other(app, None),
        KeyCode::Char(ch) if other_focused => edit_other(app, Some(ch)),
        _ => {}
    }
}

/// A digit jumps to that row and selects it, except on the free-text row which
/// only takes the cursor (Python `_handle_number_key`).
fn handle_number_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) -> bool {
    let KeyCode::Char(ch) = key.code else {
        return false;
    };
    let Some(digit) = ch.to_digit(10) else {
        return false;
    };
    let Some(option_idx) = (digit as usize).checked_sub(1) else {
        return false;
    };
    let rows = current_question(app).options.len() + usize::from(other_option_idx(app).is_some());
    if option_idx >= rows {
        return false;
    }
    if is_within_grace_period(app) {
        return true;
    }
    navigate_to_option(app, option_idx);
    if other_option_idx(app) != Some(option_idx) {
        select(app, client);
    }
    true
}

fn move_caret(app: &mut App, right: bool) {
    let text = other_text(app, app.question_app.current_question_idx).to_owned();
    let cursor = &mut app.question_app.other_cursor;
    if right {
        input_edit::cursor_right(&text, cursor);
    } else {
        input_edit::cursor_left(&text, cursor);
    }
}

/// Insert a character, or delete the one before the caret, then re-sync the
/// free-text tick (Python `on_input_changed` -> `_sync_free_choice_selection`).
fn edit_other(app: &mut App, ch: Option<char>) {
    let idx = app.question_app.current_question_idx;
    let text = app.question_app.other_texts.entry(idx).or_default();
    let cursor = &mut app.question_app.other_cursor;
    match ch {
        Some(ch) => input_edit::insert(text, cursor, &ch.to_string()),
        None => input_edit::delete_left(text, cursor),
    }
    sync_free_choice_selection(app);
}

/// In multi-select, typed free text ticks the free-text row and clearing it unticks.
fn sync_free_choice_selection(app: &mut App) {
    if !current_question(app).multi_select {
        return;
    }
    let Some(other_idx) = other_option_idx(app) else {
        return;
    };
    let idx = app.question_app.current_question_idx;
    let empty = other_text(app, idx).trim().is_empty();
    let selections = app.question_app.multi_selections.entry(idx).or_default();
    if empty {
        selections.remove(&other_idx);
    } else {
        selections.insert(other_idx);
    }
}

/// A click moves the cursor to the clicked row and, in multi-select, ticks it
/// (Python `on_click`); the free-text row is only ever ticked, never unticked.
pub fn handle_mouse(app: &mut App, event: MouseEvent) {
    match event.kind {
        MouseEventKind::ScrollUp => {
            wheel(app, true);
            return;
        }
        MouseEventKind::ScrollDown => {
            wheel(app, false);
            return;
        }
        MouseEventKind::Down(MouseButton::Left) => {}
        _ => return,
    }
    let Some((_, option_idx)) = app
        .question_app
        .option_rows
        .iter()
        .copied()
        .find(|(row, _)| *row == event.row)
    else {
        return;
    };
    set_selected_option(app, option_idx);
    if !current_question(app).multi_select || submit_option_idx(app) == Some(option_idx) {
        return;
    }
    if other_option_idx(app) == Some(option_idx) {
        select_option(app, option_idx);
    } else {
        toggle_selection(app, option_idx);
    }
}

pub(crate) fn wheel(app: &mut App, up: bool) {
    let offset = app.question_app.viewport.offset;
    app.question_app.viewport.detach_at(if up {
        offset.saturating_sub(MOUSE_SCROLL_STEP)
    } else {
        offset.saturating_add(MOUSE_SCROLL_STEP)
    });
}
