//! `/skills` list: tabs, filter, footer, keys.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::commands::CommandEvent;
use vibe_rs::hints::{self, action, key};
use vibe_rs::server::Client;
use vibe_rs::skills_browser::labels;
use vibe_rs::skills_browser::list::{self, Row};
use vibe_rs::skills_browser::{self, help, Event};

use crate::skills_browser_support::{
    catalog, catalog_row, highlighted, installed_row, opened, pin, pins, press, registry,
    registry_on, search, shadowed, shared,
};
use crate::support::{render, rows, stub};

#[test]
fn my_skills_lists_one_row_per_name_in_server_order_and_shared_the_importable_ones() {
    let installed = vec![
        registry("deploy", "project", 3, Some("latest")),
        pin("lint", "local", "project"),
        shadowed(registry("deploy", "global", 2, None)),
    ];
    let mut app = opened(installed, Some(shared()));
    let mine = [
        Row::InstalledColumns,
        installed_row("deploy"),
        installed_row("lint"),
    ];
    assert_eq!(app.skills_browser.rows(), mine);
    assert_eq!(highlighted(&app), Some(installed_row("deploy")));
    press(&mut app, &stub(), KeyCode::Tab);
    let shared_rows = [
        Row::CatalogColumns,
        catalog_row("id-alpha"),
        catalog_row("id-zeta"),
    ];
    assert_eq!(
        app.skills_browser.rows(),
        shared_rows,
        "a project pin hides deploy"
    );
    assert_eq!(
        app.skills_browser.catalog.entries()[0].name,
        "Alpha",
        "sorted case-insensitively"
    );
}

#[test]
fn a_global_pin_stays_importable_into_a_project_but_not_without_one() {
    let global = vec![registry("deploy", "global", 2, None)];
    let mut with_project = opened(global.clone(), Some(shared()));
    press(&mut with_project, &stub(), KeyCode::Tab);
    let deploy = catalog_row("id-deploy");
    assert!(with_project.skills_browser.rows().contains(&deploy));
    let mut no_project = shared();
    no_project.project_available = false;
    let mut without = opened(global, Some(no_project));
    press(&mut without, &stub(), KeyCode::Tab);
    assert!(!without.skills_browser.rows().contains(&deploy));
}

#[test]
fn registry_skills_off_means_one_list_without_tabs() {
    let mut app = opened(vec![pin("lint", "local", "global")], None);
    let installed = [Row::InstalledColumns, installed_row("lint")];
    assert_eq!(app.skills_browser.rows(), installed);
    assert!(list::tabs(&app.skills_browser).is_empty());
    press(&mut app, &stub(), KeyCode::Tab);
    assert_eq!(
        app.skills_browser.rows(),
        installed,
        "Tab has nowhere to go"
    );
}

#[test]
fn empty_tabs_say_why() {
    let shared_note = |answer: serde_json::Value| {
        let mut app = opened(Vec::new(), Some(catalog(answer)));
        assert_eq!(app.skills_browser.rows(), [Row::Note(labels::NO_SKILLS)]);
        press(&mut app, &stub(), KeyCode::Tab);
        app.skills_browser.rows().to_vec()
    };
    let signed_out = shared_note(json!({"skills": [], "authenticated": false}));
    let sign_in = Row::Note("Sign in to Mistral to browse shared skills");
    assert_eq!(signed_out, [sign_in]);
    let failed = shared_note(json!({"skills": []}));
    assert_eq!(
        failed,
        [Row::Note("Could not load the shared skills catalog")]
    );
    let none = shared_note(json!({"skills": [], "loaded": true}));
    assert_eq!(none, [Row::Note("No shared skills to add")]);
}

#[test]
fn the_filter_matches_name_or_description_in_each_tab_and_the_tabs_count_them() {
    let client = stub();
    let mut lint = pin("lint", "local", "global");
    lint["description"] = json!("Checks STYLE");
    let mut app = opened(vec![lint, pin("pdf", "plugin", "global")], Some(shared()));
    press(&mut app, &client, KeyCode::Char('/'));
    search(&mut app, &client, "lint");
    let rows = app.skills_browser.rows();
    assert!(rows.contains(&installed_row("lint")) && !rows.contains(&installed_row("pdf")));
    let labels: Vec<String> = list::tabs(&app.skills_browser)
        .into_iter()
        .map(|(_, label)| label)
        .collect();
    assert_eq!(
        labels,
        ["My Skills 1", "Shared 1"],
        "Alpha's description mentions lint"
    );
    press(&mut app, &client, KeyCode::Tab);
    assert!(
        app.skills_browser.rows().contains(&installed_row("lint")),
        "the field keeps Tab"
    );
    press(&mut app, &client, KeyCode::Esc);
    press(&mut app, &client, KeyCode::Tab);
    assert!(
        app.skills_browser.rows().contains(&catalog_row("id-alpha")),
        "the filter holds on the other tab"
    );
    press(&mut app, &client, KeyCode::Char('/'));
    search(&mut app, &client, "style");
    assert_eq!(app.skills_browser.rows(), [Row::Note(labels::NO_MATCH)]);
    search(&mut app, &client, "zzz");
    assert_eq!(app.skills_browser.rows(), [Row::Note(labels::NO_MATCH)]);
}

#[test]
fn the_list_opens_before_the_catalog_and_shared_fills_when_it_answers() {
    let client = stub();
    let mut app = App::default();
    registry_on(&mut app);
    let installed = pins(vec![registry("deploy", "global", 2, None)]);
    skills_browser::apply_event(&mut app, &client, Event::Opened(Ok(installed)));
    press(&mut app, &client, KeyCode::Tab);
    let loading = [Row::Note("Loading shared skills")];
    assert_eq!(app.skills_browser.rows(), loading);
    let generation = app.skills_browser.generation;
    let earlier = Event::Catalog {
        generation: generation - 1,
        catalog: shared(),
    };
    skills_browser::apply_event(&mut app, &client, earlier);
    assert_eq!(
        app.skills_browser.rows(),
        loading,
        "an earlier opening's catalog is dropped"
    );
    let answer = Event::Catalog {
        generation,
        catalog: shared(),
    };
    skills_browser::apply_event(&mut app, &client, answer);
    assert!(app.skills_browser.rows().contains(&catalog_row("id-alpha")));
}

#[test]
fn the_footer_offers_only_what_the_highlighted_row_does() {
    let client = stub();
    let mut locked = pin("pdf", "plugin", "global");
    locked["locked"] = json!(true);
    let mut off = pin("lint", "local", "global");
    off["enabled"] = json!(false);
    let pinned = registry("deploy", "global", 2, None);
    let mut app = opened(vec![off, locked, pinned], Some(shared()));
    let footer = |app: &App| help(&app.skills_browser);
    let (view, add) = ((key::ENTER, action::VIEW), (key::ENTER, action::ADD));
    let (enable, disable) = ((key::SPACE, action::ENABLE), (key::SPACE, action::DISABLE));
    let remove = ("d", action::REMOVE);
    let tab = (key::TAB, action::SWITCH_TAB);
    let with = |verbs: &[(&'static str, &'static str)]| {
        let tail = [tab, hints::SEARCH, hints::CLOSE];
        [[hints::NAVIGATE].as_slice(), verbs, &tail].concat()
    };
    assert_eq!(footer(&app), with(&[view, enable]));
    press(&mut app, &client, KeyCode::Down);
    assert_eq!(footer(&app), with(&[view]), "a locked skill has no switch");
    press(&mut app, &client, KeyCode::Down);
    assert_eq!(footer(&app), with(&[view, disable, remove]));
    press(&mut app, &client, KeyCode::Tab);
    assert_eq!(footer(&app), with(&[add]));
    press(&mut app, &client, KeyCode::Char('/'));
    let leave = (key::ESC, action::LEAVE_SEARCH);
    assert_eq!(
        footer(&app),
        [(key::ARROWS, action::NAVIGATE), add, leave],
        "Space, d and Tab stay with the field"
    );
}

#[test]
fn esc_leaves_the_field_then_clears_the_filter_then_closes() {
    let client = stub();
    let mut app = opened(vec![pin("lint", "local", "global")], None);
    press(&mut app, &client, KeyCode::Char('/'));
    press(&mut app, &client, KeyCode::Char('l'));
    press(&mut app, &client, KeyCode::Esc);
    assert!(!app.skills_browser.search.focused);
    assert_eq!(app.skills_browser.search.query, "l");
    press(&mut app, &client, KeyCode::Esc);
    assert_eq!(app.skills_browser.search.query, "");
    assert!(app.skills_browser.open);
    press(&mut app, &client, KeyCode::Esc);
    assert!(!app.skills_browser.open);
}

#[test]
fn arrows_and_jk_wrap_within_a_tab_and_pages_stop_at_its_ends() {
    let client = stub();
    let names = ["a", "b", "c"].map(|name| pin(name, "local", "global"));
    let mut app = opened(names.to_vec(), Some(shared()));
    press(&mut app, &client, KeyCode::Up);
    assert_eq!(
        highlighted(&app),
        Some(installed_row("c")),
        "up wraps to the tab's last"
    );
    press(&mut app, &client, KeyCode::Char('j'));
    assert_eq!(
        highlighted(&app),
        Some(installed_row("a")),
        "over the column names"
    );
    press(&mut app, &client, KeyCode::Tab);
    press(&mut app, &client, KeyCode::End);
    press(&mut app, &client, KeyCode::PageDown);
    assert_eq!(highlighted(&app), Some(catalog_row("id-zeta")));
    press(&mut app, &client, KeyCode::Home);
    assert_eq!(highlighted(&app), Some(catalog_row("id-alpha")));
}

#[test]
fn a_failed_read_stays_closed() {
    let mut app = App::default();
    let client = stub();
    let failure = Event::Opened(Err("Skill discovery failed".to_owned()));
    skills_browser::apply_event(&mut app, &client, failure);
    assert!(!app.skills_browser.open);
}

#[tokio::test]
async fn show_reads_the_installed_skills_with_the_spinner_on() {
    let (client, mut frames) = Client::stub_answering();
    let (tx, mut rx) = mpsc::channel(4);
    let mut app = App::default();
    app.session.session_id = Some("session".to_owned());
    app.command_tx = Some(tx);
    skills_browser::show(&mut app, &Arc::new(client));
    assert!(
        app.view.command_loading,
        "the spinner runs until the answer"
    );
    let wait = tokio::time::timeout(Duration::from_secs(5), frames.recv());
    let frame = wait.await.unwrap().unwrap();
    assert_eq!(frame["method"], "skills/installed");
    assert_eq!(frame["params"], json!({"sessionId": "session"}));
    let Some(CommandEvent::Skills(event)) = rx.recv().await else {
        panic!("the answer reaches the reducer as a Skills event");
    };
    assert!(
        matches!(*event, Event::Opened(Err(_))),
        "null is not a skill list"
    );
}

#[test]
fn a_narrow_footer_drops_navigation_then_tab_and_still_ends_with_esc() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    for width in [80, 64] {
        let screen = rows(&render(width, 30, |frame| app.draw(frame)));
        let footer = screen.iter().rev().find(|row| row.contains("/ search"));
        let footer = footer.expect("the footer is drawn");
        let footer = footer.trim_end_matches([' ', '│']);
        assert!(footer.ends_with("Esc close"), "{width} columns: {footer}");
    }
}
