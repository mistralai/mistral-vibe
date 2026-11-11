//! `/skills` selections beside the preview: each keeps to the half it started in, and copies as under the rows.

use std::ops::Range;

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::selection::{region, RegionId, RowSpan};
use vibe_rs::skills_browser;

use crate::skills_browser_support::{opened, pin};
use crate::support::{column, down, drag, render, rows, stub, up};

fn skills() -> Vec<Value> {
    let mut lint = pin("lint", "local", "global");
    lint["prompt"] = json!("# lint\n\nCheck the style before every commit.");
    vec![lint, pin("deploy", "local", "global")]
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
fn a_drag_from_the_list_into_the_preview_highlights_the_rows_text_alone() {
    let mut app = opened(skills(), None);
    let (x, y, _) = find(&mut app, 160, 0..80, "lint ");
    let (_, last, _) = find(&mut app, 160, 0..80, "deploy");
    let (screen, spans, text) = select(&mut app, 160, (x, y), (125, last));
    assert_eq!(text, "lint    Global  Local\n   ● deploy  Global  Local");
    assert_eq!(spans.len(), 2, "both rows are highlighted");
    for (y, _, x1) in spans {
        let cell = screen[usize::from(y)].chars().nth(usize::from(x1));
        assert!(
            cell.is_some_and(|cell| !cell.is_whitespace()),
            "row {y} ends its highlight on its text, not at the half's edge"
        );
    }
}

#[test]
fn a_preview_copies_beside_the_list_as_it_does_under_it() {
    let copy = |width: u16, columns: Range<usize>| {
        let mut app = opened(skills(), None);
        let (x, y, _) = find(&mut app, width, columns.clone(), "# lint");
        let (_, last, end) = find(&mut app, width, columns, "commit.");
        select(&mut app, width, (x, y), (end, last)).2
    };
    let under = copy(100, 0..100);
    assert_eq!(under, "# lint\n   Check the style before every commit.");
    assert_eq!(
        copy(160, 80..160),
        under,
        "a blank row is no line of a copy either way"
    );
}
