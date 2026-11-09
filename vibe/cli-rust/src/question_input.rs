//! Keyboard and mouse input for the `ask_user_question` bottom-app: the
//! question-owned keys first, then the shared composer keymap on the
//! free-text row.

use std::sync::Arc;

use crate::server::Client;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use crate::app::App;
use crate::question_app::{
    cancel, current_question, handle_other_key, is_other_selected, is_within_grace_period,
    move_down, move_up, navigate_to_option, next_question, other_caret_at, other_option_idx,
    paste_other, prev_question, select, set_selected_option, submit_other, toggle_focused,
};
use crate::selection;

/// The question app owns every key while it is open.
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
        // Only plain Space toggles.
        KeyCode::Char(' ') if !other_focused && key.modifiers == KeyModifiers::NONE => {
            toggle_focused(app)
        }
        // Only plain Enter submits; modified Enters never do.
        KeyCode::Enter if key.modifiers == KeyModifiers::NONE => submit_other(app, client),
        KeyCode::Left if app.question_app.questions.len() > 1 => prev_question(app),
        KeyCode::Right if app.question_app.questions.len() > 1 => next_question(app),
        _ => {}
    }
}

/// A digit jumps to that row and selects it, except on the free-text row which
/// only takes the cursor.
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

/// A drag selects the box text (Textual widgets are selectable); a click
/// answers an option like Enter, ticking it in multi-select. The free-text row
/// is Python's Input, which owns its mouse: no screen selection starts there, a
/// click on its text moves the caret, and a click on its prefix toggles it in
/// multi-select.
pub fn handle_mouse(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    let at = (event.column, event.row);
    let highlighted = app.question_app.list.selected();
    let click = if event.kind == MouseEventKind::Down(MouseButton::Left) && on_other_row(app, at) {
        app.question_app.list.press(at);
        None
    } else {
        crate::mouse::option_gesture(app, event, selection::RegionId::BottomApp, |app| {
            &mut app.question_app.list
        })
    };
    let Some(option_idx) = click else {
        return;
    };
    apply_click(app, client, option_idx, option_idx != highlighted, at);
    if !on_other_row(app, at) {
        return;
    }
    if let Some(offset) = other_caret_at(app, event.column, event.row) {
        app.question_app.other_cursor = offset;
    }
}

fn on_other_row(app: &App, at: (u16, u16)) -> bool {
    other_option_idx(app).is_some_and(|idx| app.question_app.list.index_at(at) == Some(idx))
}

/// The free-text row's option prefix, left of the field's text.
fn on_other_prefix(app: &App, at: (u16, u16)) -> bool {
    app.question_app
        .other_field
        .is_some_and(|field| at.0 < field.x)
}

fn apply_click(
    app: &mut App,
    client: &Arc<Client>,
    option_idx: usize,
    moved: bool,
    at: (u16, u16),
) {
    if moved {
        set_selected_option(app, option_idx);
    }
    if !is_other_selected(app) {
        select(app, client);
        return;
    }
    // The free-text row is an input: Enter answers it, and only its prefix
    // toggles it, so a typed answer can be unticked without a key.
    if on_other_prefix(app, at) {
        toggle_focused(app);
    }
}
