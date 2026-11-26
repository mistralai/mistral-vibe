//! Search highlights in a task's output, across the wraps of one line but never across two lines, the current match marked.

use ratatui::style::{Modifier, Style};

use super::{lit, Found, Styles};
use crate::tasks_panel::find::Hit;
use crate::tasks_panel::Row;

const HIT: Style = Style::new().add_modifier(Modifier::BOLD);
const CURRENT: Style = Style::new().add_modifier(Modifier::REVERSED);

/// The lit parts of each `(row, origin)`, for a query `length` bytes long.
fn lit_parts(
    rows: &[(&str, (usize, usize))],
    hits: &[Hit],
    length: usize,
    current: Option<Hit>,
) -> Vec<(String, Style)> {
    let styles = Styles {
        text: Style::new(),
        hit: HIT,
        current: CURRENT,
    };
    let found = Found {
        hits,
        length,
        current,
    };
    rows.iter()
        .flat_map(|(text, (line, start))| {
            let row = Row {
                line: *line,
                range: *start..start + text.len(),
            };
            lit(text, &row, &found, styles).spans
        })
        .filter(|span| span.style != Style::new())
        .map(|span| (span.content.into_owned(), span.style))
        .collect()
}

#[test]
fn lights_a_match_split_by_a_wrap_on_both_rows() {
    let parts = lit_parts(&[("abcde", (0, 0)), ("fgh", (0, 5))], &[(0, 3)], 3, None);
    assert_eq!(parts, [("de".to_owned(), HIT), ("f".to_owned(), HIT)]);
}

#[test]
fn never_lights_the_next_line() {
    assert!(lit_parts(&[("fgh", (1, 0))], &[(0, 3)], 3, None).is_empty());
}

#[test]
fn marks_the_current_match() {
    let parts = lit_parts(
        &[("Error and ERROR", (0, 0))],
        &[(0, 0), (0, 10)],
        5,
        Some((0, 10)),
    );
    assert_eq!(
        parts,
        [("Error".to_owned(), HIT), ("ERROR".to_owned(), CURRENT)]
    );
}

#[test]
fn marks_a_split_current_match_on_both_rows() {
    let parts = lit_parts(
        &[("abcde", (0, 0)), ("fgh", (0, 5))],
        &[(0, 3)],
        3,
        Some((0, 3)),
    );
    assert_eq!(
        parts,
        [("de".to_owned(), CURRENT), ("f".to_owned(), CURRENT)]
    );
}
