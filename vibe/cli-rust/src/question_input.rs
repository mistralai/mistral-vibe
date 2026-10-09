//! Keyboard and mouse input for the `ask_user_question` bottom-app: the
//! question-owned keys first, then the shared composer keymap on the
//! free-text row.

use std::sync::Arc;

use crate::server::Client;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use crate::app::App;
use crate::mouse::MOUSE_SCROLL_STEP;
use crate::question_app::{
    cancel, current_question, handle_other_key, is_other_selected, is_within_grace_period,
    move_down, move_up, navigate_to_option, next_question, other_caret_at, other_option_idx,
    paste_other, prev_question, select, select_option, set_selected_option, submit_option_idx,
    submit_other, toggle_focused, toggle_selection,
};
use crate::selection;

/// The question app owns every key while it is open (Python `QuestionApp.on_key`).
/// It answers Esc, Up/Down, Enter and Space itself; the focused free-text row takes the
/// chat input editing keys first, and ↑↓ leave it only from its first/last row.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let other_focused = is_other_selected(app);
    if other_focused && handle_other_key(app, key) {
        return;
    }
    if !other_focused && handle_number_key(app, client, key) {
        return;
    }
    match key.code {
        KeyCode::Esc => cancel(app, client),
        KeyCode::Up => move_up(app),
        KeyCode::Down => move_down(app),
        KeyCode::Char('k') if !other_focused => move_up(app),
        KeyCode::Char('j') if !other_focused => move_down(app),
        KeyCode::Enter if !other_focused => select(app, client),
        // Only plain Space toggles, like Textual's `space` binding.
        KeyCode::Char(' ') if !other_focused && key.modifiers == KeyModifiers::NONE => {
            toggle_focused(app)
        }
        // Only plain Enter submits (Textual `Input`); modified Enters never do.
        KeyCode::Enter if key.modifiers == KeyModifiers::NONE => submit_other(app, client),
        KeyCode::Left if app.question_app.questions.len() > 1 => prev_question(app),
        KeyCode::Right if app.question_app.questions.len() > 1 => next_question(app),
        _ => {}
    }
}

/// A digit jumps to that row and selects it, except on the free-text row which
/// only takes the cursor (Python `_handle_number_key`).
fn handle_number_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) -> bool {
    let rows = current_question(app).options.len() + usize::from(other_option_idx(app).is_some());
    let Some(option_idx) = crate::list_nav::digit(&key, rows) else {
        return false;
    };
    if is_within_grace_period(app) {
        return true;
    }
    navigate_to_option(app, option_idx);
    if other_option_idx(app) != Some(option_idx) {
        select(app, client);
    }
    true
}

/// Paste into the focused free-text row, keeping every line; a paste never
/// falls through to the composer.
pub fn handle_paste(app: &mut App, text: String) {
    if !is_other_selected(app) || text.is_empty() {
        return;
    }
    paste_other(app, &text);
}

/// A drag selects the box text (Textual widgets are selectable); a release on
/// the pressed cell runs Python `on_click`, whatever the click chain, so a
/// jittered round trip still clicks and a drag never navigates. The free-text
/// row is Python's Input, which owns its mouse: no screen selection starts
/// there, and any same-row release focuses it with the caret under the pointer.
pub fn handle_mouse(app: &mut App, event: MouseEvent) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            app.question_app.mouse_press_row = Some(event.row);
            if !pressed_other_row(app, event.row) {
                selection::press_owned(app, at, selection::RegionId::BottomApp);
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
                place_other_caret(app, at);
            }
        }
        _ => {}
    }
}

/// A click on the free-text field parks its caret under the pointer.
fn place_other_caret(app: &mut App, (column, row): (u16, u16)) {
    if !pressed_other_row(app, row) {
        return;
    }
    if let Some(offset) = other_caret_at(app, column, row) {
        app.question_app.other_cursor = offset;
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
