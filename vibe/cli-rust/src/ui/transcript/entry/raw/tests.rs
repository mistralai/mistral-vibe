use super::lines;
use crate::selection::Fold;

fn rows(source: &str, width: u16) -> (Vec<String>, Vec<Option<Fold>>) {
    let lines = lines(source, width);
    let rows = lines.lines().iter().map(ToString::to_string).collect();
    (rows, lines.folds().to_vec())
}

#[test]
fn blank_messages_paint_no_rows() {
    for source in ["", "\n\n", "  \t\n", "\x1b[0m"] {
        assert!(lines(source, 20).is_empty(), "{source:?}");
    }
}

#[test]
fn source_keeps_its_markers_indentation_and_blank_lines() {
    let (rows, folds) = rows("## Title\n\n  - nested\n> quote\n", 40);
    assert_eq!(rows, ["", "## Title", "", "  - nested", "> quote"]);
    assert!(folds.iter().all(Option::is_none));
}

#[test]
fn blank_edge_lines_paint_no_rows_and_keep_the_first_indentation() {
    let (rows, _) = rows("\n \n    code\n\nend\n\t\n", 40);
    assert_eq!(rows, ["", "    code", "", "end"]);
}

#[test]
fn wrapped_rows_fold_onto_the_line_above_without_a_hang() {
    let (rows, folds) = rows("one two three", 8);
    assert_eq!(rows, ["", "one two", "three"]);
    assert_eq!(folds[2], Fold::hung(Some(1), 0));
}

#[test]
fn an_unbreakable_word_is_cut_at_the_width() {
    let (rows, _) = rows("abcdefghij", 4);
    assert_eq!(rows, ["", "abcd", "efgh", "ij"]);
}

#[test]
fn tabs_expand_so_no_cell_is_left_unpainted() {
    let (rows, _) = rows("a\tb", 20);
    assert_eq!(rows[1], "a       b");
}
