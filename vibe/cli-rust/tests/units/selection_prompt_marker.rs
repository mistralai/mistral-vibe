//! Prompt markers are chrome only in the first column; a wrapped lone `>` is content.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;

use vibe_rs::app::App;
use vibe_rs::selection::{self, region};

fn copied(rows: &[&str]) -> String {
    let area = Rect::new(0, 0, 24, rows.len() as u16);
    let mut buffer = Buffer::empty(area);
    for (y, row) in rows.iter().enumerate() {
        buffer.set_string(0, y as u16, row, Style::default());
    }
    let mut app = App::default();
    app.view.selection_region.area = area;
    selection::press(&mut app, (0, 0));
    selection::drag(&mut app, (area.right() - 1, area.bottom() - 1));
    region::extract(&buffer, &region::spans(&app, &buffer, area))
}

#[test]
fn the_prompt_marker_is_not_copied() {
    assert_eq!(copied(&["> hello"]), "hello");
}

#[test]
fn a_wrapped_lone_closing_bracket_is_copied() {
    assert_eq!(copied(&["> <tag path=\"x\"", "  >"]), "<tag path=\"x\"\n>");
}

#[test]
fn a_quote_marker_opening_the_prompt_is_copied() {
    assert_eq!(copied(&["> > quoted"]), "> quoted");
}

#[test]
fn a_wrapped_row_opening_with_a_lone_slash_is_copied() {
    assert_eq!(copied(&["> cd", "  / tmp"]), "cd\n/ tmp");
}
