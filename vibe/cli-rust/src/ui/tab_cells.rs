//! Grapheme cells with tab stops, as Textual's TextArea expands tabs.

use unicode_width::UnicodeWidthStr;

use crate::utils::graphemes::split_graphemes;

/// Textual's TextArea `indent_width`: tabs stop every 4 columns from the line start.
const TAB_SIZE: usize = 4;
const TAB_SPACES: &str = "    ";

#[derive(Clone, Copy)]
pub struct Cell<'a> {
    pub byte: usize,
    pub symbol: &'a str,
    pub column: usize,
    pub width: usize,
}

impl<'a> Cell<'a> {
    /// What the cell draws: a tab becomes spaces up to its stop.
    pub fn shown(self) -> &'a str {
        if self.symbol.starts_with('\t') {
            return &TAB_SPACES[..self.width];
        }
        self.symbol
    }
}

/// Drawn graphemes of `text`, which starts at `column` of its logical line.
pub fn cells(text: &str, column: usize) -> impl Iterator<Item = Cell<'_>> {
    split_graphemes(text).scan(column, |column, (byte, symbol)| {
        let width = if symbol.starts_with('\t') {
            TAB_SIZE - *column % TAB_SIZE
        } else {
            symbol.width()
        };
        let cell = Cell {
            byte,
            symbol,
            column: *column,
            width,
        };
        *column += width;
        Some(cell)
    })
}

pub fn cells_width(text: &str, column: usize) -> usize {
    cells(text, column).map(|cell| cell.width).sum()
}
