//! `/skills` after an answer: where the highlight lands, and which spinner stays up.

use crossterm::event::KeyCode;
use serde_json::json;

use vibe_rs::app::App;
use vibe_rs::server::SkillUpdate;
use vibe_rs::skills_browser::page::Property;
use vibe_rs::skills_browser::{self, Event, Outcome};

use crate::skills_browser_support::{
    catalog_row, highlight, highlight_row, highlighted, installed_row, notice, open, open_page,
    opened, page as page_of, page_row, pin, pins, press, registry, session, shadowed, shared,
    versions,
};
use crate::support::{messages, stub};

fn changed(installed: Vec<serde_json::Value>) -> Event {
    Event::Changed {
        outcome: Outcome {
            answer: Some(json!({})),
            error: None,
        },
        installed: Ok(pins(installed)),
    }
}

#[test]
fn a_page_whose_skill_is_removed_lands_on_its_rows_neighbour() {
    let client = stub();
    let lint = pin("lint", "local", "global");
    let zed = pin("zed", "local", "global");
    let deploy = registry("deploy", "global", 2, None);
    let mut app = opened(vec![lint.clone(), deploy, zed.clone()], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    skills_browser::apply_event(&mut app, &client, changed(vec![lint, zed]));
    assert!(app.skills_browser.page.is_none());
    assert_eq!(highlighted(&app), Some(installed_row("zed")));
}

#[test]
fn an_install_keeps_the_highlight_where_install_was() {
    let client = stub();
    let mut app = opened(Vec::new(), Some(shared()));
    open_page(&mut app, catalog_row("id-deploy"));
    highlight_row(&mut app, Property::Install);
    let installed = vec![registry("deploy", "global", 3, Some("latest"))];
    skills_browser::apply_event(&mut app, &client, changed(installed));
    assert_eq!(page_row(&app), Some(Property::Enabled));
}

#[tokio::test]
async fn a_page_waiting_for_its_versions_keeps_its_spinner_through_other_answers() {
    let mut app = opened(Vec::new(), Some(shared()));
    let (client, _frames) = session(&mut app);
    highlight(&mut app, &catalog_row("id-deploy"));
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(app.view.loading.label(), "Loading deploy");
    app.view.command_loading = false;
    skills_browser::apply_event(&mut app, &client, Event::Updates(Ok(Vec::new())));
    assert!(app.view.command_loading, "another answer ends nothing");
    assert_eq!(app.view.loading.label(), "Loading deploy");
}

#[tokio::test]
async fn only_the_page_asked_for_last_opens() {
    let pins = vec![
        registry("deploy", "global", 2, None),
        registry("lint", "global", 1, None),
    ];
    let mut app = opened(pins.clone(), Some(shared()));
    let (client, _frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Enter);
    press(&mut app, &client, KeyCode::Char('j'));
    press(&mut app, &client, KeyCode::Enter);
    let answer = |name: &str| Event::Page {
        row: installed_row(name),
        versions: Ok(versions()),
    };
    let deploy = answer("deploy");
    skills_browser::apply_event(&mut app, &client, deploy);
    assert!(
        app.skills_browser.page.is_none(),
        "a superseded answer opens nothing"
    );
    let lint = answer("lint");
    skills_browser::apply_event(&mut app, &client, lint);
    assert_eq!(page_of(&app).subject.name(), "lint");
    press(&mut app, &client, KeyCode::Esc);
    press(&mut app, &client, KeyCode::Enter);
    press(&mut app, &client, KeyCode::Esc);
    open(&mut app, &client, pins, Some(shared()));
    let late = answer("lint");
    skills_browser::apply_event(&mut app, &client, late);
    assert!(
        app.skills_browser.page.is_none(),
        "a reopened browser ignores the old answer"
    );
}

#[test]
fn an_install_that_lands_shadowed_returns_to_its_shared_row() {
    let client = stub();
    let local = pin("deploy", "local", "global");
    let mut app = opened(vec![local.clone()], Some(shared()));
    open_page(&mut app, catalog_row("id-deploy"));
    let pinned = shadowed(registry("deploy", "global", 3, Some("latest")));
    skills_browser::apply_event(&mut app, &client, changed(vec![local, pinned]));
    assert!(page_of(&app).subject.installed());
    press(&mut app, &client, KeyCode::Esc);
    assert_eq!(highlighted(&app), Some(catalog_row("id-deploy")));
}

#[tokio::test]
async fn a_page_opened_at_once_drops_the_one_still_loading() {
    let pins = vec![
        registry("deploy", "global", 2, None),
        pin("lint", "local", "global"),
    ];
    let mut app = opened(pins, Some(shared()));
    let (client, _frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Enter);
    press(&mut app, &client, KeyCode::Char('j'));
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(page_of(&app).subject.name(), "lint");
    press(&mut app, &client, KeyCode::Esc);
    let deploy = Event::Page {
        row: installed_row("deploy"),
        versions: Ok(versions()),
    };
    skills_browser::apply_event(&mut app, &client, deploy);
    assert!(app.skills_browser.page.is_none(), "lint was asked for last");
}

#[tokio::test]
async fn a_page_opens_on_the_pins_its_answer_finds() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    let (client, _frames) = session(&mut app);
    highlight(&mut app, &catalog_row("id-deploy"));
    press(&mut app, &client, KeyCode::Enter);
    skills_browser::apply_event(&mut app, &client, changed(Vec::new()));
    let answer = Event::Page {
        row: catalog_row("id-deploy"),
        versions: Ok(versions()),
    };
    skills_browser::apply_event(&mut app, &client, answer);
    let rows = &page_of(&app).rows;
    assert!(rows.contains(&Property::Install) && !rows.contains(&Property::Remove));
}

#[tokio::test]
async fn the_list_keeps_its_spinner_until_it_opens() {
    let mut app = App::default();
    let (client, _frames) = session(&mut app);
    skills_browser::show(&mut app, &client);
    app.view.command_loading = false;
    skills_browser::apply_event(&mut app, &client, Event::Updates(Ok(Vec::new())));
    assert!(app.view.command_loading);
    assert_eq!(app.view.loading.label(), "Loading skills");
}

#[test]
fn new_versions_join_the_startup_warnings_instead_of_a_notice_that_fades() {
    let client = stub();
    let mut app = App::default();
    let updates = vec![SkillUpdate {
        name: "deploy".to_owned(),
    }];
    skills_browser::apply_event(&mut app, &client, Event::Updates(Ok(updates)));
    assert_eq!(notice(&app), None);
    let warnings = messages(&app);
    let startup = warnings.iter().find(|(role, _)| role == "startup_notices");
    assert!(
        startup.is_some_and(|(_, text)| text.contains("deploy")),
        "{warnings:?}"
    );
}

#[tokio::test]
async fn closing_while_a_page_loads_ends_its_spinner() {
    let mut app = opened(Vec::new(), Some(shared()));
    let (client, _frames) = session(&mut app);
    highlight(&mut app, &catalog_row("id-deploy"));
    press(&mut app, &client, KeyCode::Enter);
    assert!(app.view.command_loading);
    press(&mut app, &client, KeyCode::Esc);
    assert!(!app.skills_browser.open);
    assert!(!app.view.command_loading, "nobody waits for the page now");
}

#[tokio::test]
async fn closing_keeps_the_spinner_of_a_change_in_flight_under_its_label() {
    let mut app = opened(Vec::new(), Some(shared()));
    let (client, _frames) = session(&mut app);
    highlight(&mut app, &catalog_row("id-deploy"));
    press(&mut app, &client, KeyCode::Enter);
    app.skills_browser.busy = Some("Adding deploy".to_owned());
    press(&mut app, &client, KeyCode::Esc);
    assert!(app.view.command_loading);
    assert_eq!(app.view.loading.label(), "Adding deploy");
}

fn reread_failed(error: Option<&str>) -> Event {
    Event::Changed {
        outcome: Outcome {
            answer: Some(json!({})),
            error: error.map(str::to_owned),
        },
        installed: Err("server gone".to_owned()),
    }
}

#[test]
fn a_re_read_that_fails_says_so_while_the_browser_is_open() {
    let client = stub();
    let mut app = opened(vec![pin("lint", "local", "global")], None);
    skills_browser::apply_event(&mut app, &client, reread_failed(None));
    assert_eq!(notice(&app), Some("Failed to read skills: server gone"));
    assert!(
        app.skills_browser.rows().contains(&installed_row("lint")),
        "the last read stays"
    );
}

#[test]
fn a_change_answering_after_the_browser_closed_reports_only_its_own_failure() {
    let client = stub();
    let mut app = opened(vec![pin("lint", "local", "global")], None);
    press(&mut app, &client, KeyCode::Esc);
    skills_browser::apply_event(&mut app, &client, reread_failed(None));
    assert_eq!(notice(&app), None, "nobody waits for the list now");
    app.skills_browser.busy = Some("Disabling lint".to_owned());
    skills_browser::apply_event(&mut app, &client, reread_failed(Some("busy")));
    assert_eq!(notice(&app), Some("Disabling lint failed: busy"));
}
