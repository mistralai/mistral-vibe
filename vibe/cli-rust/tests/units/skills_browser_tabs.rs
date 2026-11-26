//! `/skills` tabs: My Skills and Shared, switched with Tab, Shift+Tab, ←→ or a press on one, in a box that keeps its height.

use crossterm::event::{KeyCode, MouseButton, MouseEventKind};
use ratatui::layout::Rect;
use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::focus::tab_navigates;
use vibe_rs::server::SkillCatalog;
use vibe_rs::skills_browser::list::Row;
use vibe_rs::skills_browser::{self, Tab};

use crate::skills_browser_support;
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
    press(&mut app, &client, KeyCode::Tab);
    assert_eq!(app.skills_browser.tab, Tab::Shared);
    assert_eq!(highlighted(&app), Some(catalog_row("id-alpha")));
    press(&mut app, &client, KeyCode::Tab);
    assert_eq!(app.skills_browser.tab, Tab::Mine);
    assert_eq!(highlighted(&app), Some(installed_row("deploy")));
}

#[test]
fn shift_tab_and_the_arrows_switch_tabs_but_the_search_field_keeps_its_arrows() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    for code in [KeyCode::BackTab, KeyCode::Right, KeyCode::Left] {
        let before = app.skills_browser.tab;
        press(&mut app, &client, code);
        assert_ne!(app.skills_browser.tab, before, "{code:?} switches tabs");
    }
    press(&mut app, &client, KeyCode::Char('/'));
    press(&mut app, &client, KeyCode::Right);
    assert_eq!(
        app.skills_browser.tab,
        Tab::Shared,
        "the field takes the arrow"
    );
}

#[test]
fn shift_tab_switches_skills_tabs_instead_of_cycling_agents() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    assert!(tab_navigates(&app));
    assert!(!crate::support::press(&mut app, KeyCode::BackTab));
    assert_eq!(app.agents.desired, None, "no agent switch was asked for");
    open_page(&mut app, installed_row("deploy"));
    assert!(!tab_navigates(&app), "a page has no tabs");
    let mut local = opened(vec![registry("deploy", "global", 2, None)], None);
    assert!(
        !tab_navigates(&local),
        "without registry skills there are no tabs"
    );
    local.skills_browser.open = false;
    assert!(!tab_navigates(&local));
}

#[test]
fn shift_tab_moves_through_the_delete_confirmation_instead_of_cycling_agents() {
    let mut file = skills_browser_support::pin("lint", "local", "project");
    file["path"] = json!("/repo/.vibe/skills/lint/SKILL.md");
    let mut app = opened(vec![file], None);
    assert!(!tab_navigates(&app));
    press(&mut app, &stub(), KeyCode::Char('x'));
    assert!(app.skills_browser.confirm.is_some());
    assert!(tab_navigates(&app));
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
fn a_press_on_a_tab_opens_it() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    highlight(&mut app, &installed_row("deploy"));
    app.skills_browser.tab_areas = vec![
        (Tab::Mine, Rect::new(10, 3, 11, 1)),
        (Tab::Shared, Rect::new(22, 3, 8, 1)),
    ];
    let press_at = mouse(MouseEventKind::Down(MouseButton::Left), 24, 3);
    skills_browser::mouse(&mut app, &client, press_at);
    assert_eq!(app.skills_browser.tab, Tab::Shared);
    assert!(app.skills_browser.rows().contains(&catalog_row("id-alpha")));
    assert!(!app.skills_browser.rows().contains(&Row::InstalledColumns));
}

/// Twelve shared skills, more than half the box's rows on a 40-row screen.
fn twelve_shared() -> SkillCatalog {
    let entry = |n: u8| json!({"name": format!("skill-{n:02}"), "skillId": format!("id-{n}")});
    let skills: Vec<Value> = (1..=12).map(entry).collect();
    catalog(json!({"skills": skills, "loaded": true}))
}

/// The row of the box's top border on a 120x40 screen.
fn box_top(app: &mut App) -> usize {
    let screen = rows(&render(120, 40, |frame| app.draw(frame)));
    let at = screen.iter().position(|row| row.contains("│ Skills "));
    at.expect("the box is drawn") - 1
}

#[test]
fn the_box_keeps_the_height_it_opened_with_through_the_catalog_tabs_and_search() {
    let client = stub();
    let mut app = opened(vec![registry("deploy", "global", 2, None)], None);
    skills_browser_support::registry_on(&mut app);
    app.skills_browser.registry = true;
    let opened_at = box_top(&mut app);
    let generation = app.skills_browser.generation;
    let event = skills_browser::Event::Catalog {
        generation,
        catalog: twelve_shared(),
    };
    skills_browser::apply_event(&mut app, &client, event);
    assert_eq!(box_top(&mut app), opened_at, "a late catalog");
    press(&mut app, &client, KeyCode::Tab);
    assert_eq!(box_top(&mut app), opened_at, "the longer tab");
    press(&mut app, &client, KeyCode::Char('/'));
    for ch in "zzz".chars() {
        press(&mut app, &client, KeyCode::Char(ch));
    }
    assert_eq!(
        box_top(&mut app),
        opened_at,
        "a search that matches nothing"
    );
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
    assert!(mine.ends_with("Skills   My Skills 0"), "{mine}");
    assert_eq!(
        app.skills_browser.tab_areas.len(),
        1,
        "only the open tab is pressed"
    );
    press(&mut app, &client, KeyCode::Tab);
    let shared_title = title(&mut app);
    let shared_title = shared_title.trim_end();
    assert!(shared_title.contains("Skills   Shared "), "{shared_title}");
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
