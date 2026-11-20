//! `/skills` selections: a drag across the list copies its rows, one across a page's body copies the body.

use std::ops::Range;

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::selection::{region, RegionId, RowSpan};
use vibe_rs::skills_browser;

use crate::skills_browser_support::{installed_row, open_page, opened, pin};
use crate::support::{column, down, drag, render, rows, stub, up};

fn skills() -> Vec<Value> {
    let mut lint = pin("lint", "local", "global");
    lint["prompt"] = json!("# lint\n\nCheck the style before every commit.");
    vec![lint, pin("zed", "local", "global")]
}

/// The cell where `text` first shows within `columns` of a `width` wide screen, and the column just past it.
fn find(app: &mut App, width: u16, columns: Range<usize>, text: &str) -> (u16, u16, u16) {
    let screen = rows(&render(width, 40, |frame| app.draw(frame)));
    let part = |row: &String| -> String {
        let end = row.chars().take(columns.end);
        end.skip(columns.start).collect()
    };
    let y = screen.iter().position(|row| part(row).contains(text));
    let y = y.unwrap_or_else(|| panic!("{text} is not on screen"));
    let x = columns.start as u16 + column(&part(&screen[y]), text);
    (x, y as u16, x + text.chars().count() as u16)
}

/// Drag from `start` to `end` on a `width` wide screen; the screen, the spans highlighted and the text copied.
fn select(
    app: &mut App,
    width: u16,
    start: (u16, u16),
    end: (u16, u16),
) -> (Vec<String>, Vec<RowSpan>, String) {
    let client = stub();
    skills_browser::mouse(app, &client, down(start.0, start.1));
    skills_browser::mouse(app, &client, drag(end.0, end.1));
    skills_browser::mouse(app, &client, up(end.0, end.1));
    let buffer = render(width, 40, |frame| app.draw(frame));
    let chat = region::get(app, RegionId::BottomApp).content();
    let spans = region::spans(app, &buffer, chat);
    let selection = app.selection.region.as_ref();
    let text = selection.map(|selection| selection.text.clone());
    (rows(&buffer), spans, text.unwrap_or_default())
}

#[test]
fn a_drag_across_the_list_copies_its_rows() {
    let mut app = opened(skills(), None);
    let (x, y, _) = find(&mut app, 160, 0..160, "lint ");
    let (_, last, end) = find(&mut app, 160, 0..160, "zed ");
    let (_, spans, text) = select(&mut app, 160, (x, y), (end + 20, last));
    assert_eq!(text, "lint   Global  Local\n   ● zed    Global  Local");
    assert_eq!(spans.len(), 2, "both rows are highlighted");
}

#[test]
fn a_drag_across_a_page_body_copies_the_body() {
    let mut app = opened(skills(), None);
    open_page(&mut app, installed_row("lint"));
    let (x, y, _) = find(&mut app, 100, 0..100, "Check");
    let (_, last, end) = find(&mut app, 100, 0..100, "commit.");
    let text = select(&mut app, 100, (x, y), (end, last)).2;
    assert!(
        text.contains("Check the style before every commit."),
        "{text}"
    );
}
