use ratatui::style::Style;
use unicode_width::UnicodeWidthStr;

use super::{push_wrapped, Builder};

fn wrapped(text: &str, width: u16) -> Vec<String> {
    let mut builder = Builder::new(0, 100, None);
    push_wrapped(&mut builder, text, width, Style::default());
    builder
        .finish()
        .lines
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect()
        })
        .collect()
}

#[test]
fn never_emits_an_indent_only_row() {
    assert_eq!(wrapped("    foobar baz", 8), ["foobar", "baz"]);
}

#[test]
fn keeps_an_indent_that_fits() {
    assert_eq!(wrapped("  foo bar", 6), ["  foo", "bar"]);
}

#[test]
fn folds_emoji_presentation_runs_by_grapheme_width() {
    assert_eq!(
        wrapped(&"\u{2764}\u{fe0f}".repeat(6), 4),
        vec!["\u{2764}\u{fe0f}".repeat(2); 3]
    );
}

#[test]
fn keeps_every_folded_row_within_the_width() {
    let rows = wrapped(&"\u{644}\u{627}".repeat(8), 4);
    assert!(rows.len() > 1);
    assert!(rows
        .iter()
        .all(|row| UnicodeWidthStr::width(row.as_str()) <= 4));
}
