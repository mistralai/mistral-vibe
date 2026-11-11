//! `/skills` list rows: one per name, a highlight that follows its skill, the update badge, locked registry rows.

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::hints::{action, key};
use vibe_rs::server::SkillScope;
use vibe_rs::skills_browser::labels::{self, NO_MATCH};
use vibe_rs::skills_browser::list::Row;
use vibe_rs::skills_browser::page::Property;
use vibe_rs::skills_browser::pins::update;
use vibe_rs::skills_browser::{self, help, Event, Outcome};

use crate::skills_browser_support::{
    catalog, highlight, highlighted, installed_row, next, open_page, opened, page, pin, pins,
    press, registry, search, session, shadowed, shared,
};
use crate::support::stub;

#[test]
fn an_older_server_without_the_active_marker_lists_one_row_per_name() {
    let both = vec![
        registry("deploy", "project", 3, None),
        registry("deploy", "global", 2, None),
    ];
    let app = opened(both, None);
    let rows = app.skills_browser.rows();
    let deploys = rows.iter().filter(|row| **row == installed_row("deploy"));
    assert_eq!(deploys.count(), 1);
}

#[test]
fn the_highlight_stays_on_its_skill_when_a_row_above_goes() {
    let client = stub();
    let names = ["a", "b", "c"].map(|name| pin(name, "local", "global"));
    let mut app = opened(names.to_vec(), None);
    highlight(&mut app, &installed_row("c"));
    let left = vec![pin("b", "local", "global"), pin("c", "local", "global")];
    let event = Event::Changed {
        outcome: Outcome::default(),
        installed: Ok(pins(left)),
    };
    skills_browser::apply_event(&mut app, &client, event);
    assert_eq!(highlighted(&app), Some(installed_row("c")));
}

#[test]
fn the_update_badge_clears_at_the_newest_and_never_shows_on_an_alias_pin() {
    let catalog = catalog(json!({"skills": [], "updates": {"deploy": 4}, "loaded": true}));
    let app = opened(Vec::new(), Some(catalog));
    let state = &app.skills_browser;
    let pinned = |version, alias| pins(vec![registry("deploy", "global", version, alias)]);
    assert_eq!(update(state, &pinned(2, None)[0]), Some(4));
    assert_eq!(
        update(state, &pinned(4, None)[0]),
        None,
        "already the newest"
    );
    assert_eq!(
        update(state, &pinned(2, Some("stable"))[0]),
        None,
        "an alias tracks"
    );
}

#[test]
fn a_locked_registry_row_offers_remove_but_no_switch() {
    let mut locked = registry("deploy", "global", 2, None);
    locked["locked"] = json!(true);
    let app = opened(vec![locked], Some(shared()));
    let footer = help(&app.skills_browser);
    assert!(footer.contains(&("d", action::REMOVE)));
    assert!(!footer.iter().any(|(name, _)| *name == key::SPACE));
}

#[test]
fn esc_off_a_filter_that_matched_nothing_never_closes_at_once() {
    let client = stub();
    let mut app = opened(vec![pin("lint", "local", "global")], None);
    press(&mut app, &client, KeyCode::Char('/'));
    search(&mut app, &client, "zzz");
    assert_eq!(app.skills_browser.rows(), [Row::Note(NO_MATCH)]);
    press(&mut app, &client, KeyCode::Esc);
    press(&mut app, &client, KeyCode::Esc);
    assert!(
        app.skills_browser.open,
        "Esc left the field, then cleared the filter"
    );
    assert_eq!(highlighted(&app), Some(installed_row("lint")));
    press(&mut app, &client, KeyCode::Esc);
    assert!(!app.skills_browser.open);
}

#[tokio::test]
async fn space_turns_a_disabled_skill_on() {
    let mut off = pin("lint", "local", "global");
    off["enabled"] = json!(false);
    let mut app = opened(vec![off], None);
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Char(' '));
    let (method, params) = next(&mut frames).await;
    assert_eq!(
        (method.as_str(), params["enabled"].clone()),
        ("skills/setEnabled", json!(true))
    );
}

#[test]
fn a_name_whose_pin_another_name_loads_keeps_a_row_that_reaches_it() {
    let ship = registry("ship", "project", 5, None);
    let mut launch = shadowed(registry("launch", "global", 5, None));
    launch["registry"]["skillId"] = json!("id-ship");
    let mut app = opened(vec![ship, launch], Some(shared()));
    let rows = app.skills_browser.rows().to_vec();
    assert!(rows.contains(&installed_row("ship")));
    assert!(
        rows.contains(&installed_row("launch")),
        "its own row, though the session loads the file as ship"
    );
    open_page(&mut app, installed_row("launch"));
    let state = &app.skills_browser;
    let page = page(&app);
    assert!(page.rows.contains(&Property::Remove));
    assert_eq!(
        labels::scope_tail(state, page, SkillScope::Global),
        Some(labels::SHADOWED)
    );
}

#[tokio::test]
async fn space_switches_nothing_on_a_row_whose_pin_another_name_loads() {
    let ship = registry("ship", "project", 5, None);
    let mut launch = shadowed(registry("launch", "global", 5, None));
    launch["registry"]["skillId"] = json!("id-ship");
    let mut app = opened(vec![ship, launch], Some(shared()));
    let (client, mut frames) = session(&mut app);
    highlight(&mut app, &installed_row("launch"));
    let offered = help(&app.skills_browser);
    assert!(!offered.iter().any(|(keys, _)| *keys == key::SPACE));
    press(&mut app, &client, KeyCode::Char(' '));
    let sent = tokio::time::timeout(std::time::Duration::from_millis(200), frames.recv()).await;
    assert!(sent.is_err(), "nothing is switched");
}
