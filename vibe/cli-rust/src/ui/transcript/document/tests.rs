//! Margin and fold rows recorded for the copy slice.

use ratatui::text::Line;

use super::{push_rows, Painted};
use crate::selection::Fold;

const FOLD: Fold = Fold {
    gap: 1,
    hang: 0,
    breaks: false,
};

fn rows(
    lines: &[&str],
    gaps: &[usize],
    folds: &[Option<Fold>],
    prewrapped: bool,
) -> (Vec<u16>, Vec<u16>) {
    let lines: Vec<Line<'static>> = lines
        .iter()
        .map(|line| Line::from(line.to_string()))
        .collect();
    let (mut fold_rows, mut margins) = (Vec::new(), Vec::new());
    let painted = Painted {
        lines: &lines,
        folds,
        gaps,
    };
    push_rows(
        &mut fold_rows,
        &mut margins,
        10,
        painted,
        10,
        prewrapped,
        0..u16::MAX,
    );
    (margins, fold_rows.into_iter().map(|(y, _)| y).collect())
}

#[test]
fn spacing_rows_are_margins_and_blank_content_is_not() {
    let (margins, _) = rows(&["", "", "> a", "", "  b"], &[0, 1], &[], true);
    assert_eq!(margins, vec![10, 11]);
}

#[test]
fn a_blank_line_opening_the_content_is_kept() {
    let (margins, _) = rows(&["", "", "code"], &[0], &[], true);
    assert_eq!(margins, vec![10]);
}

#[test]
fn unwrapped_whitespace_lines_count_every_row_paragraph_paints() {
    let folds = [None, None, None, Some(FOLD)];
    let (margins, folds) = rows(&["  ", "abc", "  ", "d"], &[0], &folds, false);
    assert_eq!(margins, vec![10, 11]);
    // Each whitespace line's second painted row folds into its first, so it copies as one line.
    assert_eq!(folds, vec![11, 14, 15]);
}

#[test]
fn rows_below_the_visible_range_are_not_recorded() {
    let lines = [Line::from(""), Line::from(""), Line::from("a")];
    let (mut fold_rows, mut margins) = (Vec::new(), Vec::new());
    let painted = Painted {
        lines: &lines,
        folds: &[None, Some(FOLD), Some(FOLD)],
        gaps: &[0, 1],
    };
    push_rows(&mut fold_rows, &mut margins, 10, painted, 10, true, 0..11);
    assert_eq!(margins, vec![10]);
    assert!(fold_rows.is_empty());
}
