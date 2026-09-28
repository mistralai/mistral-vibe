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
use crate::selection;
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

/// Paste into the focused free-text row, keeping only the first line (Python
/// `Input._on_paste`); a paste never falls through to the composer.
pub fn handle_paste(app: &mut App, text: String) {
    if !is_other_selected(app) || text.is_empty() {
        return;
    }
    let line = first_paste_line(&text);
    let idx = app.question_app.current_question_idx;
    let text = app.question_app.other_texts.entry(idx).or_default();
    let cursor = &mut app.question_app.other_cursor;
    input_edit::insert(text, cursor, line);
    sync_free_choice_selection(app);
}

const PASTE_LINE_BREAKS: [char; 10] = [
    '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}', '\u{2029}',
];

/// The first line of pasted text, splitting like Python's `str.splitlines`.
fn first_paste_line(text: &str) -> &str {
    text.find(|ch: char| PASTE_LINE_BREAKS.contains(&ch))
        .map_or(text, |end| &text[..end])
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

/// A drag selects the box text (Textual widgets are selectable); a release on
/// the pressed cell runs Python `on_click`, whatever the click chain, so a
/// jittered round trip still clicks and a drag never navigates. The free-text
/// row is Python's Input, which owns its mouse: no screen selection starts
/// there, and any same-row release still focuses it.
pub fn handle_mouse(app: &mut App, event: MouseEvent) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            app.question_app.mouse_press_row = Some(event.row);
            if !pressed_other_row(app, event.row) {
                selection::press_owned(app, at, selection::RegionId::Question);
            }
        }
        MouseEventKind::Drag(MouseButton::Left) => selection::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            let same_row = app
                .question_app
                .mouse_press_row
                .take()
                .is_some_and(|row| row == event.row);
            let same_cell = app.selection.press == Some(at);
            let option_idx = option_row_idx(app, event.row);
            selection::release(app);
            if same_cell || (same_row && pressed_other_row(app, event.row)) {
                click(app, option_idx);
            }
        }
        _ => {}
    }
}

fn option_row_idx(app: &App, row: u16) -> Option<usize> {
    app.question_app
        .option_rows
        .iter()
        .copied()
        .find(|(r, _)| *r == row)
        .map(|(_, idx)| idx)
}

fn pressed_other_row(app: &App, row: u16) -> bool {
    other_option_idx(app).is_some_and(|idx| option_row_idx(app, row) == Some(idx))
}

/// Move the cursor to the clicked option and, in multi-select, tick it (Python
/// `on_click`); the free-text row is only ever ticked, never unticked.
fn click(app: &mut App, option_idx: Option<usize>) {
    let Some(option_idx) = option_idx else {
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
