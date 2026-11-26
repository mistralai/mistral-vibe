//! `/raw` agent message: its Markdown source, unpadded, folded so a copy rejoins wrapped rows.

use ratatui::text::{Line, Span};

use crate::selection::{fold, Fold};
use crate::ui::markdown::LinkedLines;
use crate::ui::theme;
use crate::utils::{clean, text};

/// The source rows, each at most `width` cells; a source blank once cleaned paints none.
pub(super) fn lines(source: &str, width: u16) -> LinkedLines {
    let mut lines = LinkedLines::default();
    let source = clean::clean_output(source);
    if source.trim().is_empty() {
        return lines;
    }
    lines.push_gap();
    let rows: Vec<&str> = source.split('\n').collect();
    let blank = |row: &&str| row.trim().is_empty();
    let first = rows.iter().position(|row| !blank(row)).unwrap_or(0);
    let last = rows.iter().rposition(|row| !blank(row)).unwrap_or(0);
    let source = rows[first..=last]
        .iter()
        .map(|row| text::expand_tabs(row))
        .collect::<Vec<_>>()
        .join("\n");
    let style = theme::text(theme::foreground());
    for (row, gap) in fold::wrap_hard(&source, width as usize) {
        lines.push_folded(Line::from(Span::styled(row, style)), Fold::hung(gap, 0));
    }
    lines
}

#[cfg(test)]
mod tests;
