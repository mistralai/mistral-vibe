//! ChatInput editing policy: keys become `Action`s, `apply` runs them against the
//! text, caret, and selection. The text mechanics live in `input_edit`.

use crate::utils::input_edit::{
    cursor_left, cursor_line_end, cursor_line_start, cursor_right, cursor_word_left,
    cursor_word_right, delete_left, delete_right, delete_to_end_of_line_or_delete_line,
    delete_to_start_of_line, delete_word_left, delete_word_right, insert, line_bounds,
};

/// A chat input editing action, decoupled from any key backend. `keymap` maps a
/// terminal key to one of these; `apply` runs it. Names mirror Textual's TextArea
/// action methods for bijection.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    CursorLeft,
    CursorRight,
    CursorWordLeft,
    CursorWordRight,
    CursorLineStart,
    CursorLineEnd,
    // Shift variants: extend the selection while moving (Textual `*(select=True)`).
    SelectLeft,
    SelectRight,
    SelectWordLeft,
    SelectWordRight,
    SelectLineStart,
    SelectLineEnd,
    SelectAll,
    SelectLine,
    DeleteLeft,
    DeleteRight,
    DeleteLine,
    DeleteWordLeft,
    DeleteWordRight,
    DeleteToStartOfLine,
    DeleteToEndOfLine,
    Insert(char),
}

impl Action {
    /// True if the action can change the text (an edit) rather than only moving
    /// the caret or selection. Callers reset history navigation on edits.
    pub fn is_edit(&self) -> bool {
        matches!(
            self,
            Action::DeleteLeft
                | Action::DeleteRight
                | Action::DeleteLine
                | Action::DeleteWordLeft
                | Action::DeleteWordRight
                | Action::DeleteToStartOfLine
                | Action::DeleteToEndOfLine
                | Action::Insert(_)
        )
    }
}

/// Clamp an offset to the preceding UTF-8 character boundary in `input`.
pub fn clamp_offset(input: &str, mut offset: usize) -> usize {
    offset = offset.min(input.len());
    while !input.is_char_boundary(offset) {
        offset -= 1;
    }
    offset
}

/// Restore the chat input's byte-offset invariant after mouse or external input.
pub fn normalize_positions(input: &str, cursor: &mut usize, anchor: &mut Option<usize>) {
    *cursor = clamp_offset(input, *cursor);
    *anchor = anchor.map(|offset| clamp_offset(input, offset));
}

/// The selected byte range `anchor..cursor` (ordered), or `None` when empty.
pub fn selection_range(
    input: &str,
    cursor: usize,
    anchor: Option<usize>,
) -> Option<(usize, usize)> {
    let cursor = clamp_offset(input, cursor);
    let a = clamp_offset(input, anchor?);
    let (lo, hi) = if a <= cursor {
        (a, cursor)
    } else {
        (cursor, a)
    };
    (lo < hi).then_some((lo, hi))
}

/// The currently selected text, or `None` when the selection is empty.
pub fn selected_text(input: &str, cursor: usize, anchor: Option<usize>) -> Option<String> {
    selection_range(input, cursor, anchor).map(|(lo, hi)| input[lo..hi].to_string())
}

/// Delete the selection if any; returns whether text was removed. Collapses the
/// caret to the range start and clears the anchor.
fn delete_selection(input: &mut String, cursor: &mut usize, anchor: &mut Option<usize>) -> bool {
    match selection_range(input, *cursor, *anchor) {
        Some((lo, hi)) => {
            input.replace_range(lo..hi, "");
            *cursor = lo;
            *anchor = None;
            true
        }
        None => {
            *anchor = None;
            false
        }
    }
}

/// Delete every document line intersecting the selection, or the caret line.
fn delete_lines(
    input: &mut String,
    cursor: &mut usize,
    anchor: &mut Option<usize>,
) -> Option<String> {
    if input.is_empty() {
        *anchor = None;
        return None;
    }
    let selection_start = anchor.unwrap_or(*cursor).min(*cursor);
    let selection_end = anchor.unwrap_or(*cursor).max(*cursor);
    let (from, _) = line_bounds(input, selection_start);
    let (end_start, end) = line_bounds(input, selection_end);
    let end_column = input[end_start..selection_end].chars().count();
    let to = if selection_start < selection_end && selection_end == end_start {
        selection_end
    } else if end < input.len() {
        end + 1
    } else {
        end
    };
    let deleted = input[from..to].to_owned();
    input.replace_range(from..to, "");
    *cursor = from.min(input.len());
    for _ in 0..end_column {
        cursor_right(input, cursor);
    }
    *anchor = None;
    (!deleted.is_empty()).then_some(deleted)
}

/// Cut the selected text, or the current line when the selection is empty.
pub fn cut(input: &mut String, cursor: &mut usize, anchor: &mut Option<usize>) -> Option<String> {
    normalize_positions(input, cursor, anchor);
    if let Some((lo, hi)) = selection_range(input, *cursor, *anchor) {
        let deleted = input[lo..hi].to_owned();
        delete_selection(input, cursor, anchor);
        return Some(deleted);
    }
    delete_lines(input, cursor, anchor)
}

/// Begin (or keep) a selection anchored at the caret before a Shift-move.
fn anchor_selection(cursor: usize, anchor: &mut Option<usize>) {
    if anchor.is_none() {
        *anchor = Some(cursor);
    }
}

/// Run an `Action` against the chat input text, caret, and selection anchor.
pub fn apply(action: &Action, input: &mut String, cursor: &mut usize, anchor: &mut Option<usize>) {
    normalize_positions(input, cursor, anchor);
    match action {
        // Plain caret moves drop any selection.
        Action::CursorLeft => {
            *anchor = None;
            cursor_left(input, cursor);
        }
        Action::CursorRight => {
            *anchor = None;
            cursor_right(input, cursor);
        }
        Action::CursorWordLeft => {
            *anchor = None;
            cursor_word_left(input, cursor);
        }
        Action::CursorWordRight => {
            *anchor = None;
            cursor_word_right(input, cursor);
        }
        Action::CursorLineStart => {
            *anchor = None;
            cursor_line_start(input, cursor);
        }
        Action::CursorLineEnd => {
            *anchor = None;
            cursor_line_end(input, cursor);
        }
        // Shift moves anchor the selection then move the caret.
        Action::SelectLeft => {
            anchor_selection(*cursor, anchor);
            cursor_left(input, cursor);
        }
        Action::SelectRight => {
            anchor_selection(*cursor, anchor);
            cursor_right(input, cursor);
        }
        Action::SelectWordLeft => {
            anchor_selection(*cursor, anchor);
            cursor_word_left(input, cursor);
        }
        Action::SelectWordRight => {
            anchor_selection(*cursor, anchor);
            cursor_word_right(input, cursor);
        }
        Action::SelectLineStart => {
            anchor_selection(*cursor, anchor);
            cursor_line_start(input, cursor);
        }
        Action::SelectLineEnd => {
            anchor_selection(*cursor, anchor);
            cursor_line_end(input, cursor);
        }
        Action::SelectAll => {
            *anchor = Some(0);
            *cursor = input.len();
        }
        Action::SelectLine => {
            let (start, end) = line_bounds(input, *cursor);
            *anchor = Some(start);
            *cursor = end;
        }
        // Edits replace the selection first, if any.
        Action::DeleteLeft => {
            if !delete_selection(input, cursor, anchor) {
                delete_left(input, cursor);
            }
        }
        Action::DeleteRight => {
            if !delete_selection(input, cursor, anchor) {
                delete_right(input, cursor);
            }
        }
        Action::DeleteLine => {
            delete_lines(input, cursor, anchor);
        }
        Action::DeleteWordLeft => {
            if !delete_selection(input, cursor, anchor) {
                delete_word_left(input, cursor);
            }
        }
        Action::DeleteWordRight => {
            if !delete_selection(input, cursor, anchor) {
                delete_word_right(input, cursor);
            }
        }
        Action::DeleteToStartOfLine => {
            if !delete_selection(input, cursor, anchor) {
                delete_to_start_of_line(input, cursor);
            }
        }
        Action::DeleteToEndOfLine => {
            if !delete_selection(input, cursor, anchor) {
                delete_to_end_of_line_or_delete_line(input, cursor);
            }
        }
        Action::Insert(c) => {
            delete_selection(input, cursor, anchor);
            insert(input, cursor, c.encode_utf8(&mut [0; 4]));
        }
    }
}
