//! Soft-wrapped rows copy as the line they display, not as the rows they paint.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;

use vibe_rs::selection::fold::{self, Fold, Folds};
use vibe_rs::ui::markdown::command_result_linked;

fn buffer(rows: &[&str]) -> Buffer {
    let mut buf = Buffer::empty(Rect::new(0, 0, 12, rows.len() as u16));
    for (y, row) in rows.iter().enumerate() {
        buf.set_string(0, y as u16, row, Style::default());
    }
    buf
}

#[test]
fn gaps_mark_word_wraps_as_folds() {
    let gaps = fold::gaps("alpha beta gamma", &["alpha beta", "gamma"]);
    assert_eq!(gaps, vec![None, Some(1)]);
}

#[test]
fn gaps_keep_newlines_as_line_breaks() {
    let gaps = fold::gaps("one\n\ntwo", &["one", "", "two"]);
    assert_eq!(gaps, vec![None, None, None]);
}

#[test]
fn gaps_count_the_spaces_a_character_wrap_leaves_at_the_row_end() {
    assert_eq!(fold::gaps("ab  cd", &["ab  ", "cd"]), vec![None, Some(2)]);
    assert_eq!(fold::gaps("abcdef", &["abc", "def"]), vec![None, Some(0)]);
}

#[test]
fn gaps_keep_the_leading_spaces_a_continuation_row_paints() {
    assert_eq!(fold::gaps("a   b", &["a ", "  b"]), vec![None, Some(1)]);
}

#[test]
fn gaps_give_up_on_rows_the_source_does_not_hold() {
    let gaps = fold::gaps("alpha beta", &["alpha", "bet…", "beta"]);
    assert_eq!(gaps, vec![None, None, None]);
}

#[test]
fn extract_rejoins_folded_rows_without_their_hang() {
    let buf = buffer(&["• one two", "  three", "next"]);
    let folds = Folds {
        x: 0,
        rows: vec![(
            1,
            Fold {
                gap: 1,
                hang: 2,
                breaks: false,
            },
        )],
    };
    let spans = [(0, 0, 11), (1, 0, 11), (2, 0, 11)];
    assert_eq!(fold::extract(&buf, &spans, &folds), "• one two three\nnext");
}

#[test]
fn extract_drops_the_hang_of_an_indented_row_but_keeps_its_line_break() {
    let buf = buffer(&["$ for f; do", "    echo $f", "  done"]);
    let folds = Folds {
        x: 0,
        rows: vec![(1, Fold::indented(2)), (2, Fold::indented(2))],
    };
    let spans = [(0, 0, 11), (1, 0, 11), (2, 0, 11)];
    assert_eq!(
        fold::extract(&buf, &spans, &folds),
        "$ for f; do\n  echo $f\ndone"
    );
}

#[test]
fn extract_breaks_a_fold_whose_row_above_is_not_selected() {
    let buf = buffer(&["one two", "", "three"]);
    let folds = Folds {
        x: 0,
        rows: vec![(
            2,
            Fold {
                gap: 1,
                hang: 0,
                breaks: false,
            },
        )],
    };
    assert_eq!(
        fold::extract(&buf, &[(0, 0, 11), (2, 0, 11)], &folds),
        "one two\nthree"
    );
}

#[test]
fn extract_reads_wide_glyphs_once() {
    let buf = buffer(&["中文 ok"]);
    assert_eq!(
        fold::extract(&buf, &[(0, 0, 11)], &Folds::default()),
        "中文 ok"
    );
}

#[test]
fn wrapped_list_items_copy_as_one_line_each() {
    let text = "- alpha beta gamma delta epsilon\n  - zeta eta theta iota kappa";
    let linked = command_result_linked(text, 20);
    let area = Rect::new(0, 0, 20, linked.len() as u16);
    let mut buf = Buffer::empty(area);
    for (y, line) in linked.lines().iter().enumerate() {
        buf.set_line(0, y as u16, line, area.width);
    }
    let rows = linked
        .folds()
        .iter()
        .enumerate()
        .filter_map(|(y, fold)| Some((y as u16, (*fold)?)))
        .collect();
    let spans: Vec<_> = (0..area.height).map(|y| (y, 2, 19)).collect();
    let copied = fold::extract(&buf, &spans, &Folds { x: 0, rows });
    assert_eq!(
        copied.trim(),
        "• alpha beta gamma delta epsilon\n  ▪ zeta eta theta iota kappa"
    );
}

#[test]
fn gaps_open_lines_for_a_source_that_starts_with_newlines() {
    let gaps = fold::gaps("\n\nb", &["", "", "b"]);
    assert_eq!(gaps, vec![None, None, None]);
}

#[test]
fn extract_adds_no_gap_for_a_selection_ending_inside_the_hang() {
    let buf = buffer(&["• one two", "  three"]);
    let folds = Folds {
        x: 0,
        rows: vec![(
            1,
            Fold {
                gap: 1,
                hang: 2,
                breaks: false,
            },
        )],
    };
    assert_eq!(
        fold::extract(&buf, &[(0, 0, 11), (1, 0, 1)], &folds),
        "• one two"
    );
}
