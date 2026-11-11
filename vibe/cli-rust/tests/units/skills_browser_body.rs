//! `/skills` body pane: kept in place per screen, scrolled apart from the rows by pages, the wheel and its scrollbar.

use crossterm::event::KeyCode;
use ratatui::layout::Rect;
use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::mouse;
use vibe_rs::server::SkillScope;
use vibe_rs::skills_browser::bodies::{self, BodyPane, Content};
use vibe_rs::skills_browser::model::Choice;
use vibe_rs::skills_browser::page::Property;
use vibe_rs::skills_browser::picker::{self, PickerRow};
use vibe_rs::skills_browser::{self, Event, Kind, Outcome};

use crate::skills_browser_support::{
    answer, highlight_pick, highlight_row, installed_row, next, open_page, open_page_with, opened,
    page, page_row, picker_row, pin, pins, press, registry, session, shadowed, shared,
};
use crate::support::{down, drag, render, rows, stub, up};

/// The body pane as a render would leave it: 50 lines, 10 on screen.
fn rendered(app: &mut App) -> &mut BodyPane {
    let body = &mut app.skills_browser.pane;
    body.lines = 50;
    body.visible = 10;
    body
}

#[test]
fn pages_scroll_the_body_and_leave_the_rows_where_they_are() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    press(&mut app, &client, KeyCode::End);
    assert_eq!(page_row(&app), Some(Property::Remove));
    rendered(&mut app);
    press(&mut app, &client, KeyCode::PageDown);
    assert_eq!(app.skills_browser.pane.scroll, 10);
    assert_eq!(page_row(&app), Some(Property::Remove), "the rows stay put");
    for _ in 0..5 {
        press(&mut app, &client, KeyCode::PageDown);
    }
    assert_eq!(app.skills_browser.pane.scroll, 40, "the last line stops it");
    press(&mut app, &client, KeyCode::PageUp);
    assert_eq!(app.skills_browser.pane.scroll, 30);
    skills_browser::wheel_body(&mut app, true, 2);
    assert_eq!(app.skills_browser.pane.scroll, 32);
    skills_browser::scroll_body_to(&mut app, 100);
    assert_eq!(app.skills_browser.pane.scroll, 40);
}

#[tokio::test]
async fn a_picker_starts_each_preview_at_its_top() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let (client, _frames) = session(&mut app);
    rendered(&mut app).scroll = 20;
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(
        app.skills_browser.pane.scroll, 0,
        "the picker opens on its preview's top"
    );
    rendered(&mut app);
    press(&mut app, &client, KeyCode::PageDown);
    assert_eq!(app.skills_browser.pane.scroll, 10);
    press(&mut app, &client, KeyCode::Char('j'));
    assert_eq!(
        app.skills_browser.pane.scroll, 0,
        "another version starts at its top"
    );
    rendered(&mut app).scroll = 20;
    press(&mut app, &client, KeyCode::Esc);
    let body = &app.skills_browser.pane;
    let shown = body
        .shown
        .as_ref()
        .map(|shown| (shown.screen, shown.content.clone()));
    let global = Content::Pin {
        name: "deploy".to_owned(),
        scope: "global".to_owned(),
        source: "registry".to_owned(),
    };
    assert_eq!(
        shown,
        Some((Kind::Page, global)),
        "back on the scope row's own pin"
    );
    assert_eq!(body.scroll, 0);
}

#[test]
fn the_unpin_row_previews_the_version_its_scope_pins() {
    let client = stub();
    let both = vec![
        registry("deploy", "project", 3, None),
        shadowed(registry("deploy", "global", 2, None)),
    ];
    let mut app = opened(both, Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    press(&mut app, &client, KeyCode::Enter);
    highlight_pick(&mut app, &PickerRow::Unpin { inert: false });
    let key = picker::previewed(&app.skills_browser, page(&app));
    assert_eq!(key, Some(("id-deploy".to_owned(), Some(2))));
}

#[tokio::test]
async fn a_change_that_moves_the_picker_highlight_fetches_its_preview() {
    let both = vec![
        registry("deploy", "project", 3, None),
        shadowed(registry("deploy", "global", 2, None)),
    ];
    let mut app = opened(both, Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let (client, mut frames) = session(&mut app);
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    press(&mut app, &client, KeyCode::Enter);
    let (_, params) = next(&mut frames).await;
    assert_eq!(params["version"], 2);
    answer(&mut app, &client, "id-deploy", 2);
    highlight_pick(&mut app, &PickerRow::Unpin { inert: false });
    let remaining = vec![registry("deploy", "global", 2, None)];
    let changed = Event::Changed {
        outcome: Outcome::default(),
        installed: Ok(pins(remaining)),
    };
    skills_browser::apply_event(&mut app, &client, changed);
    let (method, params) = next(&mut frames).await;
    assert_eq!(
        (method.as_str(), params["version"].clone()),
        ("skills/detail", 3.into())
    );
}

#[test]
fn a_picker_on_the_version_the_page_shows_still_starts_at_its_top() {
    let client = stub();
    let mut app = opened(Vec::new(), Some(shared()));
    open_page(
        &mut app,
        crate::skills_browser_support::catalog_row("id-deploy"),
    );
    rendered(&mut app).scroll = 20;
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(app.skills_browser.pane.scroll, 0);
}

#[test]
fn a_row_showing_the_same_pin_keeps_the_body_where_it_was() {
    let client = stub();
    let both = vec![
        registry("deploy", "project", 3, None),
        shadowed(registry("deploy", "global", 2, None)),
    ];
    let mut app = opened(both, Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    press(&mut app, &client, KeyCode::Char('j'));
    assert_eq!(page_row(&app), Some(Property::Scope(SkillScope::Project)));
    rendered(&mut app).scroll = 20;
    press(&mut app, &client, KeyCode::Char('j'));
    assert_eq!(page_row(&app), Some(Property::Enabled));
    assert_eq!(
        app.skills_browser.pane.scroll, 20,
        "Enabled shows the project pin too"
    );
    press(&mut app, &client, KeyCode::Char('k'));
    press(&mut app, &client, KeyCode::Char('k'));
    assert_eq!(
        app.skills_browser.pane.scroll, 0,
        "the global pin starts at its top"
    );
}

#[test]
fn a_picker_keeps_its_pane_whatever_its_highlight_shows() {
    let client = stub();
    let both = vec![
        registry("deploy", "project", 1, None),
        shadowed(registry("deploy", "global", 2, None)),
    ];
    let mut app = opened(both, Some(shared()));
    open_page_with(&mut app, installed_row("deploy"), Vec::new());
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    press(&mut app, &client, KeyCode::Enter);
    let pane = |app: &App| {
        let state = &app.skills_browser;
        (bodies::shown(state).is_some(), bodies::reserved(state))
    };
    let latest = PickerRow::Pick(Choice::Alias("latest".to_owned()));
    assert_eq!(picker_row(&app), Some(latest));
    assert_eq!(
        pane(&app),
        (false, true),
        "nothing to preview, and the pane keeps its place"
    );
    press(&mut app, &client, KeyCode::Char('j'));
    assert_eq!(picker_row(&app), Some(PickerRow::Unpin { inert: false }));
    assert_eq!(pane(&app), (true, true));
}

#[test]
fn dragging_the_body_scrollbar_scrolls_the_body() {
    let mut deploy = pin("deploy", "local", "global");
    let prompt: Vec<String> = (1..=60).map(|n| format!("line {n:02}")).collect();
    deploy["prompt"] = json!(prompt.join("\n"));
    let mut app = opened(vec![deploy], None);
    let screen = rows(&render(100, 40, |frame| app.draw(frame)));
    let top = screen.iter().position(|row| row.contains("line 01"));
    let top = top.expect("the body shows its first line") as u16;
    let tracks = mouse::scrollbar_tracks(&app, Rect::new(0, top, 100, 1));
    let [track] = tracks[..] else {
        panic!("one scrollbar beside the body: {tracks:?}");
    };
    mouse::route(&mut app, down(track.x, track.y));
    mouse::route(&mut app, drag(track.x, track.bottom() - 1));
    mouse::route(&mut app, up(track.x, track.bottom() - 1));
    let pane = &app.skills_browser.pane;
    assert!(pane.scroll > 0, "the drag scrolled the body");
    assert_eq!(
        pane.scroll,
        pane.lines - pane.visible,
        "down to its last line"
    );
}

#[test]
fn a_change_to_the_pin_on_screen_shows_its_new_body() {
    let client = stub();
    let deploy = |prompt: &str| {
        let mut deploy = pin("deploy", "local", "global");
        deploy["prompt"] = json!(prompt);
        deploy
    };
    let mut app = opened(vec![deploy("# deploy v1")], None);
    let screen = |app: &mut App| rows(&render(100, 40, |frame| app.draw(frame))).join("\n");
    assert!(screen(&mut app).contains("# deploy v1"));
    let changed = Event::Changed {
        outcome: Outcome::default(),
        installed: Ok(pins(vec![deploy("# deploy v2")])),
    };
    skills_browser::apply_event(&mut app, &client, changed);
    let after = screen(&mut app);
    assert!(
        after.contains("# deploy v2") && !after.contains("# deploy v1"),
        "the pane wraps the body it shows now"
    );
}

#[test]
fn the_bodies_an_opening_fetched_go_when_it_closes() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    answer(&mut app, &client, "id-deploy", 2);
    assert!(
        !app.skills_browser.bodies.is_empty(),
        "the body is kept while open"
    );
    press(&mut app, &client, KeyCode::Esc);
    assert!(
        app.skills_browser.bodies.is_empty(),
        "closing drops what the opening fetched"
    );
}
