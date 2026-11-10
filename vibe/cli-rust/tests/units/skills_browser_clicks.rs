//! `/skills` clicks that do only what was clicked: Enter confirms a removal, and a double-click that opens a screen stops there.

use std::time::Duration;

use crossterm::event::KeyCode;
use vibe_rs::skills_browser;

use crate::skills_browser_support::{
    cell_of, installed_row, open_page, opened, pin, press, registry, session, shared, through,
};
use crate::support::{down, render, up};

#[tokio::test]
async fn a_click_on_remove_arms_it_and_only_enter_confirms() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let (client, mut frames) = session(&mut app);
    let (column, row) = cell_of(&mut app, "Remove");
    for _ in 0..2 {
        skills_browser::mouse(&mut app, &client, down(column, row));
        skills_browser::mouse(&mut app, &client, up(column, row));
        render(100, 40, |frame| app.draw(frame));
    }
    assert_eq!(app.skills_browser.removing.as_deref(), Some("deploy"));
    let early = tokio::time::timeout(Duration::from_millis(200), frames.recv()).await;
    assert!(early.is_err(), "two clicks removed nothing");
    press(&mut app, &client, KeyCode::Enter);
    let methods: Vec<String> = through(&mut frames, "skills/installed")
        .await
        .into_iter()
        .map(|(method, _)| method)
        .collect();
    assert_eq!(methods, ["skills/remove", "skills/installed"]);
}

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
