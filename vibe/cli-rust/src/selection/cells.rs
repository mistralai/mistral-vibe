//! Cell predicates and word/paragraph endpoint motions shared by selection surfaces.

use ratatui::buffer::Buffer;

use crate::utils::input_edit::is_word;

/// True when the cell holds a `\w` char (Python's word-boundary regex).
pub fn is_word_cell(buf: &Buffer, x: u16, y: u16) -> bool {
    buf.cell((x, y))
        .and_then(|cell| cell.symbol().chars().next())
        .is_some_and(is_word)
}

/// True when the cell is empty or whitespace.
pub fn is_blank(buf: &Buffer, x: u16, y: u16) -> bool {
    buf.cell((x, y))
        .map(|cell| cell.symbol().trim().is_empty())
        .unwrap_or(true)
}

/// Widen the left endpoint to the start of its word, never past `min`.
pub fn word_start(buf: &Buffer, mut x0: u16, min: u16, y: u16) -> u16 {
    while x0 > min && is_word_cell(buf, x0 - 1, y) {
        x0 -= 1;
    }
    x0
}

/// Widen the right endpoint to the end of its word, never past `max`; a non-word
/// cell stays put (Python `_snap_endpoint` only extends from inside a word).
pub fn word_end(buf: &Buffer, mut x1: u16, max: u16, y: u16) -> u16 {
    if is_word_cell(buf, x1, y) {
        while x1 < max && is_word_cell(buf, x1 + 1, y) {
            x1 += 1;
        }
    }
    x1
}

/// First non-blank column at or after `x0`, never past `max`.
pub fn skip_blanks_right(buf: &Buffer, mut x0: u16, max: u16, y: u16) -> u16 {
    while x0 < max && is_blank(buf, x0, y) {
        x0 += 1;
    }
    x0
}

/// Last non-blank column at or before `x1`, never before `min`.
pub fn trim_blanks_left(buf: &Buffer, mut x1: u16, min: u16, y: u16) -> u16 {
    while x1 > min && is_blank(buf, x1, y) {
        x1 -= 1;
    }
    x1
}
