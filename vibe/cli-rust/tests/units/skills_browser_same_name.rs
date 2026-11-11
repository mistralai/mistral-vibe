//! `/skills` with two shared skills under one name: each page and each removal touches only its own pins.

use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::server::SkillScope;
use vibe_rs::skills_browser::page::Property;
use vibe_rs::skills_browser::pins::{page_pin, scope_choice};
use vibe_rs::skills_browser::{self, labels, Event, Outcome};

use crate::skills_browser_support::{
    catalog, catalog_row, cell_of, highlight, highlight_row, installed_row, open_page, opened,
    page, page_row, pins, press, registry, session, shadowed, through,
};
use crate::support::{down, render, rows, stub, up};

/// `review` twice: shared skill `id-review` pinned in the project, `id-other` pinned globally and shadowed by it.
fn two_reviews() -> App {
    let project = registry("review", "project", 3, None);
    let mut global = shadowed(registry("review", "global", 1, None));
    global["registry"]["skillId"] = json!("id-other");
    let shared = catalog(json!({
        "skills": [
            {"name": "review", "skillId": "id-review", "description": "Ours", "latestVersion": 3},
            {"name": "review", "skillId": "id-other", "description": "Theirs", "latestVersion": 1},
        ],
        "loaded": true,
        "projectAvailable": true,
        "authenticated": true,
    }));
    opened(vec![project, global], Some(shared))
}

/// The scopes the `skills/remove` requests before the re-read name.
async fn removed_scopes(frames: &mut tokio::sync::mpsc::UnboundedReceiver<Value>) -> Vec<Value> {
    through(frames, "skills/installed")
        .await
        .into_iter()
        .filter(|(method, _)| method == "skills/remove")
        .map(|(_, params)| params["scope"].clone())
        .collect()
}

#[test]
fn a_same_named_skill_s_page_shows_only_its_own_pins() {
    let mut app = two_reviews();
    open_page(&mut app, catalog_row("id-other"));
    let state = &app.skills_browser;
    let page = page(&app);
    assert_eq!(
        scope_choice(state, page, SkillScope::Project),
        None,
        "the project pin is the other skill's"
    );
    assert!(scope_choice(state, page, SkillScope::Global).is_some());
    let shown = page_pin(state, page).and_then(|pin| pin.registry_ref());
    assert_eq!(
        shown.map(|registry| registry.skill_id.as_str()),
        Some("id-other")
    );
}

#[tokio::test]
async fn remove_on_a_page_leaves_a_same_named_skill_s_pins() {
    let mut app = two_reviews();
    open_page(&mut app, catalog_row("id-other"));
    let (client, mut frames) = session(&mut app);
    highlight_row(&mut app, Property::Remove);
    press(&mut app, &client, KeyCode::Enter);
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(removed_scopes(&mut frames).await, [json!("global")]);
}

#[tokio::test]
async fn d_twice_on_the_list_removes_only_the_row_s_skill() {
    let mut app = two_reviews();
    let (client, mut frames) = session(&mut app);
    highlight(&mut app, &installed_row("review"));
    press(&mut app, &client, KeyCode::Char('d'));
    press(&mut app, &client, KeyCode::Char('d'));
    assert_eq!(removed_scopes(&mut frames).await, [json!("project")]);
}

#[test]
fn only_the_page_of_the_skill_its_name_loads_offers_enabled() {
    let mut app = two_reviews();
    open_page(&mut app, catalog_row("id-other"));
    assert!(
        !page(&app).rows.contains(&Property::Enabled),
        "the switch would turn off the other skill"
    );
    let mut app = two_reviews();
    open_page(&mut app, installed_row("review"));
    assert!(page(&app).rows.contains(&Property::Enabled));
}

#[test]
fn a_same_named_skill_not_added_yet_starts_in_the_scope_the_other_leaves_free() {
    let shared = catalog(json!({
        "skills": [
            {"name": "review", "skillId": "id-review", "description": "Ours", "latestVersion": 3},
            {"name": "review", "skillId": "id-other", "description": "Theirs", "latestVersion": 1},
        ],
        "loaded": true,
        "projectAvailable": true,
        "authenticated": true,
    }));
    let mut app = opened(vec![registry("review", "global", 3, None)], Some(shared));
    open_page(&mut app, catalog_row("id-other"));
    let (state, page) = (&app.skills_browser, page(&app));
    assert_eq!(
        scope_choice(state, page, SkillScope::Global),
        None,
        "the global name is the other skill's"
    );
    assert!(scope_choice(state, page, SkillScope::Project).is_some());
    assert_eq!(
        page_row(&app),
        Some(Property::Scope(SkillScope::Project)),
        "the page opens on the scope it can pin"
    );
}

#[tokio::test]
async fn a_scope_another_skill_of_the_name_holds_says_so_and_takes_no_pin() {
    let mut app = two_reviews();
    open_page(&mut app, catalog_row("id-other"));
    let (client, mut frames) = session(&mut app);
    let screen = rows(&render(100, 40, |frame| app.draw(frame)));
    let held = "Project  held by another skill named review";
    assert!(screen.iter().any(|line| line.contains(held)), "{screen:#?}");
    let blurb = labels::blurb(SkillScope::Project);
    assert!(
        !screen.iter().any(|line| line.contains(blurb)),
        "{screen:#?}"
    );
    let (column, row) = cell_of(&mut app, "held by another");
    skills_browser::mouse(&mut app, &client, down(column, row));
    skills_browser::mouse(&mut app, &client, up(column, row));
    assert!(page(&app).picker.is_none(), "a click opens no picker there");
    highlight_row(&mut app, Property::Scope(SkillScope::Global));
    press(&mut app, &client, KeyCode::Down);
    assert_eq!(
        page_row(&app),
        Some(Property::Remove),
        "the arrows step over it"
    );
    let sent = tokio::time::timeout(Duration::from_millis(200), frames.recv()).await;
    assert!(sent.is_err(), "nothing was pinned");
}

#[test]
fn a_scope_another_skill_takes_meanwhile_gives_up_the_highlight() {
    let ours = registry("review", "global", 1, None);
    let project = catalog(json!({"skills": [], "loaded": true, "projectAvailable": true}));
    let mut app = opened(vec![ours.clone()], Some(project));
    open_page(&mut app, installed_row("review"));
    highlight_row(&mut app, Property::Scope(SkillScope::Project));
    let mut theirs = registry("review", "project", 1, None);
    theirs["registry"]["skillId"] = json!("id-other");
    let installed = Ok(pins(vec![shadowed(ours), theirs]));
    let outcome = Outcome {
        answer: Some(json!({})),
        error: None,
    };
    let changed = Event::Changed { outcome, installed };
    skills_browser::apply_event(&mut app, &stub(), changed);
    assert_eq!(page_row(&app), Some(Property::Scope(SkillScope::Global)));
}
