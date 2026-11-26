//! `/skills` mouse: a double-click that opens a screen stops there, and the body's scrollbar drags.

use std::time::Duration;

use crossterm::event::KeyCode;
use ratatui::layout::Rect;
use serde_json::json;
use vibe_rs::{mouse, skills_browser};

use crate::skills_browser_support::{
    cell_of, installed_row, open_page, opened, pin, press, session,
};
use crate::support::{down, drag, render, rows, up};

#[tokio::test]
async fn a_double_click_that_opens_a_page_stops_there() {
    let skills = vec![
        pin("lint", "local", "global"),
        pin("fmt", "local", "global"),
    ];
    let mut app = opened(skills, None);
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Char('j'));
    let (column, row) = cell_of(&mut app, "fmt");
    for _ in 0..2 {
        skills_browser::mouse(&mut app, &client, down(column, row));
        skills_browser::mouse(&mut app, &client, up(column, row));
        render(100, 40, |frame| app.draw(frame));
    }
    assert!(
        app.skills_browser.page.is_some(),
        "the first click opened the page"
    );
    let sent = tokio::time::timeout(Duration::from_millis(200), frames.recv()).await;
    assert!(sent.is_err(), "the second press switched nothing");
}

#[test]
fn dragging_the_body_scrollbar_scrolls_the_body() {
    let mut deploy = pin("deploy", "local", "global");
    let prompt: Vec<String> = (1..=60).map(|n| format!("line {n:02}")).collect();
    deploy["prompt"] = json!(prompt.join("\n\n"));
    let mut app = opened(vec![deploy], None);
    open_page(&mut app, installed_row("deploy"));
    let screen = rows(&render(100, 40, |frame| app.draw(frame)));
    let top = screen.iter().position(|row| row.contains("line 01"));
    let top = top.expect("the body shows its first line") as u16;
    let tracks = mouse::scrollbar_tracks(&app, Rect::new(0, top, 100, 1));
    let [track] = tracks[..] else {
        panic!("one scrollbar beside the body: {tracks:?}");
    };
    mouse::route(&mut app, down(track.x, track.y));
    mouse::route(&mut app, drag(track.x, track.bottom() + 5));
    mouse::route(&mut app, up(track.x, track.bottom() + 5));
    let pane = &app.skills_browser.pane;
    assert!(pane.scroll > 0, "the drag scrolled the body");
    assert_eq!(
        pane.scroll,
        pane.lines - pane.visible,
        "down to its last line"
    );
}
