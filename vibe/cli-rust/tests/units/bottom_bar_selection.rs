//! Bottom-bar selection snaps to words and rows through the shared click chain.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;

use vibe_rs::selection::Granularity;
use vibe_rs::ui::bottom_bar;

const ROW: &str = "/tmp/myproject [PID 4242]";

fn buffer() -> (Buffer, Rect) {
    let area = Rect::new(0, 0, 40, 1);
    let mut buf = Buffer::empty(area);
    buf.set_string(0, 0, ROW, Style::default());
    (buf, area)
}

fn text(lo: u16, hi: u16, granularity: Granularity) -> String {
    let (buf, area) = buffer();
    let (x0, x1) = bottom_bar::selected_columns(&buf, area, lo, hi, granularity);
    (x0..=x1)
        .map(|x| buf[(x, 0)].symbol().to_string())
        .collect::<String>()
        .trim_end()
        .to_string()
}

#[test]
fn a_double_click_selects_the_whole_word_under_the_pointer() {
    // Column 8 sits inside "myproject" (indices 5..=13).
    assert_eq!(text(8, 8, Granularity::Word), "myproject");
}

#[test]
fn a_word_click_stops_at_non_word_boundaries() {
    // Column 21 sits inside the PID digits, bounded by a space and a bracket.
    assert_eq!(text(21, 21, Granularity::Word), "4242");
}

#[test]
fn a_triple_click_selects_the_whole_row() {
    assert_eq!(text(8, 8, Granularity::Paragraph), ROW);
}

#[test]
fn a_char_drag_keeps_the_raw_column_range() {
    assert_eq!(text(5, 8, Granularity::Char), "mypr");
}
