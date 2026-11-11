use ratatui::style::Style;

use super::{fold, text, wrap_indented, Sc};

fn rows(chunks: &[Vec<Sc>]) -> Vec<String> {
    chunks
        .iter()
        .map(|row| row.iter().map(|(character, _)| character).collect())
        .collect()
}

#[test]
fn continuation_rows_keep_the_first_rows_indent() {
    let wrapped = wrap_indented("  alpha beta gamma", Style::default(), 13);
    assert_eq!(rows(&wrapped), ["  alpha beta", "  gamma"]);
}

#[test]
fn a_word_longer_than_the_line_folds_under_the_indent() {
    let wrapped = wrap_indented("    abcdefghij", Style::default(), 8);
    assert_eq!(rows(&wrapped), ["    abcd", "    efgh", "    ij"]);
}

#[test]
fn a_blank_line_stays_one_blank_row() {
    assert_eq!(rows(&wrap_indented("", Style::default(), 10)), [""]);
}

#[test]
fn wide_glyphs_fold_by_the_columns_they_fill() {
    let folded = fold(text("日本語です", Style::default()), 5);
    assert_eq!(rows(&folded), ["日本", "語で", "す"]);
}

#[test]
fn an_indent_deeper_than_half_the_line_is_cut_to_half() {
    let line = format!("{}deep text", " ".repeat(20));
    let wrapped = wrap_indented(&line, Style::default(), 12);
    assert_eq!(rows(&wrapped), ["      deep", "      text"]);
}
