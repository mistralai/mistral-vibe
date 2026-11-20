use ratatui::text::{Line, Span};

use super::rows;
use crate::hints::{action, key};
use crate::ui::hint_line::inline_spans;

fn text(line: &Line) -> String {
    line.spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect()
}

fn loading_line() -> Vec<Span<'static>> {
    let mut spans = vec![Span::raw("Generating… "), Span::raw("(12s ")];
    spans.extend(inline_spans(&[
        (key::ESC, action::INTERRUPT),
        (key::ENTER, action::STEER),
    ]));
    spans.push(Span::raw(")"));
    spans
}

#[test]
fn a_line_that_fits_stays_on_one_row() {
    let lines = rows(loading_line(), 80);
    assert_eq!(
        lines.iter().map(text).collect::<Vec<_>>(),
        ["Generating… (12s Esc to interrupt · Enter to steer)"]
    );
}

#[test]
fn a_narrow_line_breaks_before_a_hint_and_drops_the_separator() {
    let lines = rows(loading_line(), 36);
    assert_eq!(
        lines.iter().map(text).collect::<Vec<_>>(),
        ["Generating… (12s Esc to interrupt", "Enter to steer)"]
    );
}
