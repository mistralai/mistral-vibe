//! A narrower cell replacing a wide grapheme repaints the columns the grapheme covered.

use ratatui::buffer::{Buffer, CellDiffOption};
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use vibe_rs::wide_cells::WideCells;

const AREA: Rect = Rect::new(0, 0, 4, 1);

fn wide() -> Buffer {
    let mut buffer = Buffer::empty(AREA);
    buffer.set_string(0, 0, "萎", Style::new());
    buffer
}

fn caret() -> Buffer {
    let mut buffer = Buffer::empty(AREA);
    buffer.set_string(0, 0, " ", Style::new().add_modifier(Modifier::REVERSED));
    buffer
}

fn updated_columns(prev: &Buffer, next: &Buffer) -> Vec<u16> {
    prev.diff(next).into_iter().map(|(x, _, _)| x).collect()
}

#[test]
fn caret_over_a_deleted_wide_grapheme_rewrites_its_second_column() {
    let mut wide_cells = WideCells::default();
    let prev = wide();
    wide_cells.refresh(&mut prev.clone());
    let mut next = caret();
    wide_cells.refresh(&mut next);
    assert_eq!(updated_columns(&prev, &next), [0, 1]);
}

#[test]
fn unchanged_wide_graphemes_add_no_writes() {
    let mut wide_cells = WideCells::default();
    let prev = wide();
    wide_cells.refresh(&mut prev.clone());
    let mut next = wide();
    wide_cells.refresh(&mut next);
    assert!(updated_columns(&prev, &next).is_empty());
}

#[test]
fn uncovered_columns_keep_their_own_diff_option() {
    let mut wide_cells = WideCells::default();
    wide_cells.refresh(&mut wide());
    let mut next = caret();
    next[(1, 0)].set_diff_option(CellDiffOption::Skip);
    wide_cells.refresh(&mut next);
    assert_eq!(next[(1, 0)].diff_option, CellDiffOption::Skip);
}
