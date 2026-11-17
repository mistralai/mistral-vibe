//! Free-text field editing: keys, paste, vertical caret moves and click-to-caret.

use crossterm::event::{KeyCode, KeyEvent};

use super::{current_question, is_other_selected, other_option_idx, other_text};
use crate::app::App;
use crate::chat_input::apply;
use crate::keymap;
use crate::ui::question_other::field_layout;
use crate::utils::input_edit;

/// Screen geometry of the free-text field, published by the last frame.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct OtherField {
    /// Screen column where the answer starts, right after the option prefix.
    pub x: u16,
    /// Screen row of the first wrapped row, even when it is scrolled out of view.
    pub y: i32,
    /// Cells left for the answer after the prefix, the last one kept for the caret.
    pub text_width: u16,
}

const PASTE_LINE_BREAKS: [char; 9] = [
    '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}', '\u{2029}',
];

/// The single-select question was answered in the free-text field, which still holds that answer.
pub fn holds_answer(app: &App) -> bool {
    let idx = app.question_app.current_question_idx;
    !current_question(app).multi_select
        && app
            .question_app
            .answers
            .get(&idx)
            .is_some_and(|(answer, is_other)| *is_other && answer == other_text(app, idx).trim())
}

/// The focused free-text row shows its submitted answer read-only, so ←→ switch questions.
pub fn other_locked(app: &App) -> bool {
    is_other_selected(app) && !app.question_app.other_unlocked && holds_answer(app)
}

/// The focused free-text row takes typing: it is not showing a locked answer.
pub fn other_typing(app: &App) -> bool {
    is_other_selected(app) && !other_locked(app)
}

/// The focused free-text row edits a submitted answer, unlocked or changed: Esc restores it.
pub fn other_editing(app: &App) -> bool {
    let idx = app.question_app.current_question_idx;
    is_other_selected(app)
        && !current_question(app).multi_select
        && matches!(app.question_app.answers.get(&idx), Some((_, true)))
        && other_typing(app)
}

/// Unlock the submitted answer for editing, the caret at its end.
pub fn edit_other(app: &mut App) {
    app.question_app.other_unlocked = true;
    app.question_app.other_cursor = other_text(app, app.question_app.current_question_idx).len();
}

/// Restore the submitted answer being edited and lock its row again; `false` when not editing.
pub fn discard_other_edit(app: &mut App) -> bool {
    if !other_editing(app) {
        return false;
    }
    let state = &mut app.question_app;
    let idx = state.current_question_idx;
    let answer = state.answers[&idx].0.clone();
    state.other_cursor = answer.len();
    state.other_texts.insert(idx, answer);
    state.other_unlocked = false;
    true
}

/// Edit the focused free-text field; `false` leaves the key to the option navigation.
pub fn handle_other_key(app: &mut App, key: KeyEvent) -> bool {
    let handled = edit_field(app, key);
    if handled {
        app.question_app.list.follow();
    }
    handled
}

fn edit_field(app: &mut App, key: KeyEvent) -> bool {
    if matches!(key.code, KeyCode::Up | KeyCode::Down) {
        return move_vertically(app, key.code == KeyCode::Down);
    }
    let Some(action) = keymap::action_for(&key) else {
        return false;
    };
    let idx = app.question_app.current_question_idx;
    let before = other_text(app, idx).to_owned();
    let mut text = before.clone();
    // The field draws no selection, so the anchor is discarded.
    let mut anchor = None;
    apply(
        &action,
        &mut text,
        &mut app.question_app.other_cursor,
        &mut anchor,
    );
    // Only a real value change stores and re-syncs.
    if text != before {
        app.question_app.other_texts.insert(idx, text);
        sync_free_choice_selection(app);
    }
    true
}

/// Move the caret one wrapped row, or report that it sits on the field's edge;
/// Up from the end of the answer always leaves for the option above.
fn move_vertically(app: &mut App, down: bool) -> bool {
    let at_end = app.question_app.other_cursor
        >= other_text(app, app.question_app.current_question_idx).len();
    if !down && at_end {
        return false;
    }
    let Some(field) = app.question_app.other_field else {
        return false;
    };
    let (cursor, moved) = field_layout(app, field.text_width).vertical_offset(down);
    if moved {
        app.question_app.other_cursor = cursor;
    }
    moved
}

/// Insert pasted text at the caret, every line break normalised to `\n`.
pub fn paste_other(app: &mut App, text: &str) {
    let text = text.replace("\r\n", "\n").replace(PASTE_LINE_BREAKS, "\n");
    let idx = app.question_app.current_question_idx;
    let answer = app.question_app.other_texts.entry(idx).or_default();
    input_edit::insert(answer, &mut app.question_app.other_cursor, &text);
    sync_free_choice_selection(app);
    app.question_app.list.follow();
}

/// The answer offset under a screen cell of the field, or `None` above it or on
/// its option prefix.
pub fn other_caret_at(app: &App, column: u16, row: u16) -> Option<usize> {
    let field = app.question_app.other_field?;
    let visual_row = usize::try_from(i32::from(row) - field.y).ok()?;
    let column = usize::from(column.checked_sub(field.x)?);
    Some(field_layout(app, field.text_width).offset_at(visual_row, column))
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
