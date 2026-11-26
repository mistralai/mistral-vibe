use ratatui::style::{Color, Style};
use ratatui::text::Span;

use super::super::DiffRow;
use super::{banded, wrap_row};

fn row(gutter_width: u16, spans: Vec<Span<'static>>) -> DiffRow {
    DiffRow {
        border: Style::default(),
        band: None,
        gutter_width,
        spans,
    }
}

fn texts(rows: &[(Vec<Span<'static>>, Option<u16>)]) -> Vec<String> {
    rows.iter()
        .map(|(spans, _)| spans.iter().map(|span| span.content.as_ref()).collect())
        .collect()
}

#[test]
fn wraps_at_words_and_hangs_under_the_gutter() {
    let row = row(7, vec![Span::raw("  41 + "), Span::raw("one two three")]);
    assert_eq!(
        texts(&wrap_row(&row, 15)),
        ["  41 + one two", "       three"]
    );
}

#[test]
fn wraps_gutterless_gap_rows_whole() {
    assert_eq!(texts(&wrap_row(&row(0, vec![Span::raw("⋯")]), 10)), ["⋯"]);
}

#[test]
fn banded_clips_to_width_and_pads_the_band() {
    let band = Some(Color::Red);
    let clipped: String = banded(&[Span::raw("界界界")], band, 5)
        .iter()
        .map(|span| span.content.as_ref())
        .collect();
    assert_eq!(clipped, "界界 ");
    let padded = banded(&[Span::raw("ab")], band, 4);
    assert_eq!(
        padded
            .last()
            .map(|span| (span.content.as_ref(), span.style.bg)),
        Some(("  ", band))
    );
}
