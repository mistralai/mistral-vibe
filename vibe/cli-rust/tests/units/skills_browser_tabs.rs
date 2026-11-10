//! `/skills` tabs: My Skills and Shared, switched with Tab or a press on one, in a box that keeps its height.

use crossterm::event::{KeyCode, MouseButton, MouseEventKind};
use ratatui::layout::Rect;
use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::server::SkillCatalog;
use vibe_rs::skills_browser::list::Row;
use vibe_rs::skills_browser::{self, Tab};

use crate::skills_browser_support::{
    catalog, catalog_row, highlight, highlighted, installed_row, open_page, opened, press,
    registry, shared,
};
use crate::support::{mouse, render, rows, stub};

#[test]
fn tab_switches_tabs_each_from_its_first_row() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    assert_eq!(app.skills_browser.tab, Tab::Mine);
    press(&mut app, &client, KeyCode::Char('d'));
    assert!(app.skills_browser.removing.is_some());
    press(&mut app, &client, KeyCode::Tab);
    assert_eq!(app.skills_browser.tab, Tab::Shared);
    assert_eq!(highlighted(&app), Some(catalog_row("id-alpha")));
    assert!(app.skills_browser.removing.is_none(), "switching disarms");
    press(&mut app, &client, KeyCode::Tab);
    assert_eq!(app.skills_browser.tab, Tab::Mine);
    assert_eq!(highlighted(&app), Some(installed_row("deploy")));
}

#[test]
fn back_from_a_shared_skills_page_lands_on_the_shared_tab() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, catalog_row("id-zeta"));
    app.skills_browser.tab = Tab::Mine;
    press(&mut app, &client, KeyCode::Esc);
    assert_eq!(app.skills_browser.tab, Tab::Shared);
    assert_eq!(highlighted(&app), Some(catalog_row("id-zeta")));
}

#[test]
fn a_press_on_a_tab_opens_it_and_disarms_a_removal() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    highlight(&mut app, &installed_row("deploy"));
    press(&mut app, &client, KeyCode::Char('d'));
    assert!(app.skills_browser.removing.is_some());
    app.skills_browser.tab_areas = vec![
        (Tab::Mine, Rect::new(10, 3, 11, 1)),
        (Tab::Shared, Rect::new(22, 3, 8, 1)),
    ];
    let press_at = mouse(MouseEventKind::Down(MouseButton::Left), 24, 3);
    skills_browser::mouse(&mut app, &client, press_at);
    assert_eq!(app.skills_browser.tab, Tab::Shared);
    assert!(app.skills_browser.rows().contains(&catalog_row("id-alpha")));
    assert!(!app.skills_browser.rows().contains(&Row::InstalledColumns));
    assert!(app.skills_browser.removing.is_none());
}

/// Twelve shared skills, more than half the box's rows on a 40-row screen.
fn twelve_shared() -> SkillCatalog {
    let entry = |n: u8| json!({"name": format!("skill-{n:02}"), "skillId": format!("id-{n}")});
    let skills: Vec<Value> = (1..=12).map(entry).collect();
    catalog(json!({"skills": skills, "loaded": true}))
}

#[test]
fn tab_never_moves_the_box_or_its_tabs_under_or_beside_the_body() {
    for width in [120, 160] {
        let client = stub();
        let mut app = opened(Vec::new(), Some(twelve_shared()));
        let title = |app: &mut App| {
            let screen = rows(&render(width, 40, |frame| app.draw(frame)));
            let at = screen.iter().position(|row| row.contains("│ Skills "));
            let at = at.expect("the box is drawn");
            (at, screen[at].trim_end_matches([' ', '│']).to_owned())
        };
        let (mine_at, mine) = title(&mut app);
        assert!(mine.ends_with("Skills  [My Skills]   Shared"), "{mine}");
        press(&mut app, &client, KeyCode::Tab);
        let (shared_at, shared_title) = title(&mut app);
        assert_eq!(
            shared_at, mine_at,
            "an empty tab keeps the room the other needs at {width} columns"
        );
        assert!(
            shared_title.ends_with("Skills   My Skills   [Shared]"),
            "{shared_title}"
        );
    }
}

#[test]
fn tab_in_the_search_field_stays_with_the_field() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    press(&mut app, &client, KeyCode::Char('/'));
    press(&mut app, &client, KeyCode::Tab);
    assert_eq!(app.skills_browser.tab, Tab::Mine);
    assert!(
        app.skills_browser.search.focused,
        "Esc leaves the field first"
    );
}

#[test]
fn a_title_row_too_narrow_for_both_tabs_shows_the_open_one() {
    let client = stub();
    let mut app = opened(Vec::new(), Some(shared()));
    let title = |app: &mut App| {
        let screen = rows(&render(30, 40, |frame| app.draw(frame)));
        let row = screen.iter().find(|row| row.contains("│ Skills "));
        let row = row.expect("the box is drawn");
        row.trim_end_matches([' ', '│']).to_owned()
    };
    let mine = title(&mut app);
    assert!(mine.ends_with("Skills  [My Skills]"), "{mine}");
    assert_eq!(
        app.skills_browser.tab_areas.len(),
        1,
        "only the open tab is pressed"
    );
    press(&mut app, &client, KeyCode::Tab);
    let shared_title = title(&mut app);
    assert!(shared_title.ends_with("Skills  [Shared]"), "{shared_title}");
}

#[test]
fn a_press_on_the_open_tab_keeps_the_highlight() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    press(&mut app, &client, KeyCode::Tab);
    press(&mut app, &client, KeyCode::Char('j'));
    let before = highlighted(&app);
    app.skills_browser.tab_areas = vec![
        (Tab::Mine, Rect::new(10, 3, 11, 1)),
        (Tab::Shared, Rect::new(22, 3, 8, 1)),
    ];
    let press_at = mouse(MouseEventKind::Down(MouseButton::Left), 24, 3);
    skills_browser::mouse(&mut app, &client, press_at);
    assert_eq!(app.skills_browser.tab, Tab::Shared);
    assert_eq!(highlighted(&app), before);
}
