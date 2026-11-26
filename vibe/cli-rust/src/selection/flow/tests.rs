use ratatui::buffer::Buffer;
use ratatui::layout::Rect;

use super::{resolve, Flow};
use crate::selection::Granularity;

const AREA: Rect = Rect::new(0, 0, 20, 2);

/// Two transcript rows: an indented list item and a `> ` quote opening the first column.
fn buffer() -> Buffer {
    let mut buffer = Buffer::empty(AREA);
    buffer.set_string(0, 0, "    - nested", ratatui::style::Style::default());
    buffer.set_string(0, 1, "> quote", ratatui::style::Style::default());
    buffer
}

fn spans_at(raw: &[(u16, u16)], granularity: Granularity) -> Vec<(u16, u16, u16)> {
    let flow = Flow {
        selection: ((6, 0), (19, 1)),
        origin: 0,
        end_exclusive: false,
        chrome: true,
    };
    resolve(flow, granularity, &buffer(), AREA, &[], raw, false)
}

fn spans(raw: &[(u16, u16)]) -> Vec<(u16, u16, u16)> {
    let flow = Flow {
        selection: ((0, 0), (19, 1)),
        origin: 0,
        end_exclusive: false,
        chrome: true,
    };
    resolve(flow, Granularity::Char, &buffer(), AREA, &[], raw, false)
}

#[test]
fn transcript_chrome_skips_padding_and_a_first_column_quote_marker() {
    assert_eq!(spans(&[]), [(0, 2, 11), (1, 2, 6)]);
}

#[test]
fn raw_rows_select_every_painted_cell_from_the_first_column() {
    assert_eq!(spans(&[(0, 2)]), [(0, 0, 11), (1, 0, 6)]);
}

#[test]
fn a_row_selection_keeps_a_raw_row_indentation_and_skips_formatted_padding() {
    assert_eq!(spans_at(&[(0, 2)], Granularity::Paragraph)[0], (0, 0, 11));
    assert_eq!(spans_at(&[], Granularity::Paragraph)[0], (0, 4, 11));
}
