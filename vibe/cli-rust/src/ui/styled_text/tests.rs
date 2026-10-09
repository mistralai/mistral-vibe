use ratatui::style::{Color, Style};
use ratatui::text::Span;

use super::wrap_hard;

fn rows(spans: &[Span<'static>], width: usize) -> Vec<String> {
    wrap_hard(spans, width)
        .iter()
        .map(|row| row.iter().map(|span| span.content.as_ref()).collect())
        .collect()
}

fn plain(text: &str) -> Vec<Span<'static>> {
    vec![Span::raw(text.to_owned())]
}

#[test]
fn breaks_between_words() {
    assert_eq!(
        rows(&plain("alpha beta gamma"), 11),
        ["alpha beta", "gamma"]
    );
}

#[test]
fn continuation_rows_do_not_start_with_spaces() {
    assert_eq!(rows(&plain("aaa   bbb"), 4), ["aaa", "bbb"]);
}

#[test]
fn keeps_leading_indentation_on_the_first_row() {
    assert_eq!(rows(&plain("    foo bar"), 8), ["    foo", "bar"]);
}

#[test]
fn cuts_only_words_wider_than_the_row() {
    assert_eq!(rows(&plain("x abcdefghij"), 4), ["x", "abcd", "efgh", "ij"]);
}

#[test]
fn keeps_each_span_style_across_breaks() {
    let red = Style::default().fg(Color::Red);
    let blue = Style::default().fg(Color::Blue);
    let wrapped = wrap_hard(
        &[Span::styled("alpha ", red), Span::styled("beta", blue)],
        5,
    );
    assert_eq!(
        wrapped,
        [
            vec![Span::styled("alpha", red)],
            vec![Span::styled("beta", blue)]
        ]
    );
}

#[test]
fn keeps_an_indent_with_its_first_word() {
    assert_eq!(
        rows(&plain("            return some_function_name(arg)"), 30),
        ["            return", "some_function_name(arg)"]
    );
    assert_eq!(rows(&plain("    abcdef"), 6), ["    ab", "cdef"]);
}

#[test]
fn wraps_wide_glyphs_by_cell_width() {
    assert_eq!(rows(&plain("界界 界界界"), 5), ["界界", "界界", "界"]);
}

#[test]
fn keeps_one_empty_row_for_empty_input() {
    assert_eq!(rows(&[], 4), [""]);
}

#[test]
fn never_emits_an_indent_only_row() {
    assert_eq!(rows(&plain("            ab"), 10), ["  ab"]);
}
