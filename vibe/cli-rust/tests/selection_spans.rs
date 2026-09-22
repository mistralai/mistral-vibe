//! Regression: dragging in the left margin of an indented chrome row panicked
//! with "attempt to subtract with overflow" while trimming trailing blanks.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;

use vibe_rs::app::{App, Selection};
use vibe_rs::selection::{
    self, region, Granularity, RegionId, TableCellHit, TableCellKey, TableCellSpan,
};

fn selected(row: &str, from: u16, to: u16) -> Vec<(u16, u16, u16)> {
    selected_as(row, from, to, Granularity::Char)
}

fn selected_as(row: &str, from: u16, to: u16, granularity: Granularity) -> Vec<(u16, u16, u16)> {
    selected_owner(row, from, to, granularity, RegionId::Main)
}

fn selected_toast(row: &str, from: u16, to: u16) -> Vec<(u16, u16, u16)> {
    selected_owner(row, from, to, Granularity::Char, RegionId::Toast(1))
}

fn selected_owner(
    row: &str,
    from: u16,
    to: u16,
    granularity: Granularity,
    owner: RegionId,
) -> Vec<(u16, u16, u16)> {
    let chat = Rect::new(0, 0, 20, 1);
    let mut buf = Buffer::empty(chat);
    buf.set_string(0, 0, row, Style::default());
    let mut app = App::default();
    match owner {
        RegionId::Main => app.view.selection_region.area = chat,
        RegionId::Toast(_) => app.view.toast_selection_region.area = chat,
    }
    app.selection.region = Some(Selection {
        owner,
        anchor: (from, 0),
        head: (to, 0),
        pending_copy: false,
        edge_scroll: 0,
        table_cell: None,
        text: String::new(),
    });
    app.selection.granularity = granularity;
    region::spans(&app, &buf, chat)
}

fn extracted(rows: &[&str]) -> String {
    let chat = Rect::new(0, 0, 24, rows.len() as u16);
    let mut buf = Buffer::empty(chat);
    for (y, row) in rows.iter().enumerate() {
        buf.set_string(0, y as u16, row, Style::default());
    }
    let mut app = App::default();
    app.view.selection_region.area = chat;
    app.selection.region = Some(Selection {
        owner: RegionId::Main,
        anchor: (0, 0),
        head: (chat.right() - 1, i32::from(chat.bottom() - 1)),
        pending_copy: false,
        edge_scroll: 0,
        table_cell: None,
        text: String::new(),
    });
    region::extract(&buf, &region::spans(&app, &buf, chat))
}

#[test]
fn drag_left_of_an_indented_tool_header_selects_nothing() {
    assert_eq!(selected("    ⏵ Read file", 0, 2), Vec::new());
}

#[test]
fn drag_over_an_indented_tool_header_skips_its_chrome() {
    assert_eq!(selected("    ⏵ Read file", 0, 14), vec![(0, 6, 14)]);
}

#[test]
fn grouped_diff_selection_skips_nested_borders_and_line_number_gutter() {
    let chat = Rect::new(0, 0, 24, 1);
    let mut buf = Buffer::empty(chat);
    buf.set_string(0, 0, "  ⎢   ⎢    4 + BETA", Style::default());
    let mut app = App::default();
    app.view.selection_region.area = chat;
    app.view.diff_hitmap.push((0, 1, 7));
    app.selection.region = Some(Selection {
        owner: RegionId::Main,
        anchor: (0, 0),
        head: (chat.right() - 1, 0),
        pending_copy: false,
        edge_scroll: 0,
        table_cell: None,
        text: String::new(),
    });

    assert_eq!(region::spans(&app, &buf, chat), vec![(0, 15, 18)]);
}

#[test]
fn triple_click_skips_leading_indentation() {
    assert_eq!(
        selected_as("      let value = 1;", 10, 10, Granularity::Paragraph),
        vec![(0, 6, 19)]
    );
}

#[test]
fn selection_drops_ui_padding_but_preserves_content_indentation() {
    assert_eq!(
        extracted(&[
            "  fn main() {",
            "      let value = 1;",
            "      return value;",
            "  }",
        ]),
        "fn main() {\n    let value = 1;\n    return value;\n}"
    );
}

#[test]
fn copy_skips_decorative_rules() {
    assert_eq!(
        extracted(&["  before", "────────", "  after"]),
        "before\nafter"
    );
}

#[test]
fn toast_copy_keeps_leading_chrome_glyphs() {
    assert_eq!(selected_toast("> /path failed", 0, 19), vec![(0, 0, 13)]);
}

#[test]
fn toast_copy_keeps_content_indentation() {
    assert_eq!(selected_toast("  indented", 0, 19), vec![(0, 0, 9)]);
}

#[test]
fn toast_copy_keeps_a_dash_only_row() {
    assert_eq!(selected_toast("────────", 0, 19), vec![(0, 0, 7)]);
}

#[test]
fn toast_copy_ignores_transcript_selection_chrome() {
    let chat = Rect::new(0, 0, 20, 1);
    let mut buf = Buffer::empty(chat);
    buf.set_string(0, 0, "hello world", Style::default());
    let mut app = App::default();
    app.view.toast_selection_region.area = chat;
    app.view.selection_chrome.push((0, 0, 4));
    app.selection.region = Some(Selection {
        owner: RegionId::Toast(1),
        anchor: (0, 0),
        head: (chat.right() - 1, 0),
        pending_copy: false,
        edge_scroll: 0,
        table_cell: None,
        text: String::new(),
    });

    assert_eq!(region::spans(&app, &buf, chat), vec![(0, 0, 10)]);
}

#[test]
fn toast_copy_ignores_diff_gutters() {
    let chat = Rect::new(0, 0, 24, 1);
    let mut buf = Buffer::empty(chat);
    buf.set_string(0, 0, ">  line failed", Style::default());
    let mut app = App::default();
    app.view.toast_selection_region.area = chat;
    app.view.diff_hitmap.push((0, 1, 7));
    app.selection.region = Some(Selection {
        owner: RegionId::Toast(1),
        anchor: (0, 0),
        head: (chat.right() - 1, 0),
        pending_copy: false,
        edge_scroll: 0,
        table_cell: None,
        text: String::new(),
    });

    assert_eq!(region::spans(&app, &buf, chat), vec![(0, 0, 13)]);
}

#[test]
fn partial_table_cell_selection_ignores_non_overlapping_wrapped_rows() {
    let chat = Rect::new(0, 0, 120, 3);
    let buf = Buffer::empty(chat);
    let mut app = App::default();
    app.view.selection_region.area = chat;
    app.view.table_hitmap.push(TableCellHit {
        key: TableCellKey {
            entry_id: "assistant".to_owned(),
            table: 0,
            row: 1,
            column: 1,
        },
        area: Rect::new(10, 0, 82, 2),
        spans: vec![
            TableCellSpan {
                y: 0,
                x0: 11,
                x1: 90,
                text_start: 0,
                text_end: 80,
            },
            TableCellSpan {
                y: 1,
                x0: 11,
                x1: 80,
                text_start: 80,
                text_end: 150,
            },
        ],
        text: "x".repeat(150).into(),
    });

    selection::press(&mut app, (11, 0));
    selection::drag(&mut app, (16, 0));

    assert_eq!(region::spans(&app, &buf, chat), vec![(0, 11, 16)]);
}

#[test]
fn table_selection_autoscroll_uses_the_clamped_cell_head() {
    let chat = Rect::new(0, 0, 40, 12);
    let mut app = App::default();
    app.view.selection_region.area = chat;
    app.view
        .transcript_scrollbar
        .update(Rect::new(39, 0, 1, 12), 100, 12, 40);
    app.view.table_hitmap.push(TableCellHit {
        key: TableCellKey {
            entry_id: "assistant".to_owned(),
            table: 0,
            row: 1,
            column: 1,
        },
        area: Rect::new(10, 5, 8, 2),
        spans: vec![
            TableCellSpan {
                y: 5,
                x0: 11,
                x1: 15,
                text_start: 0,
                text_end: 5,
            },
            TableCellSpan {
                y: 6,
                x0: 11,
                x1: 15,
                text_start: 5,
                text_end: 10,
            },
        ],
        text: "abcdefghij".into(),
    });

    selection::press(&mut app, (11, 5));
    selection::drag(&mut app, (11, 0));
    assert_eq!(app.selection.region.as_ref().unwrap().edge_scroll, 0);

    selection::drag(&mut app, (11, 11));
    assert_eq!(app.selection.region.as_ref().unwrap().edge_scroll, 0);
}

#[test]
fn transcript_selection_autoscrolls_outside_the_viewport() {
    let chat = Rect::new(0, 2, 40, 8);
    let mut app = App::default();
    app.view.selection_region.area = chat;
    app.view
        .transcript_scrollbar
        .update(Rect::new(39, 2, 1, 8), 100, 8, 40);

    selection::press(&mut app, (10, 5));
    selection::drag(&mut app, (10, 0));
    assert_eq!(app.selection.region.as_ref().unwrap().edge_scroll, -3);

    selection::drag(&mut app, (10, 12));
    assert_eq!(app.selection.region.as_ref().unwrap().edge_scroll, 3);
}
