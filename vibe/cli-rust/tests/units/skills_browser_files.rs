//! `/skills` and skill files: the ones that failed to load, `e` to open one, `n` to ask for a new one, and changes a turn holds.

use std::path::PathBuf;

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use vibe_rs::hints::action;
use vibe_rs::server::InstalledSkills;
use vibe_rs::skills_browser::list::Row;
use vibe_rs::skills_browser::{self, help, Event, Outcome};

use crate::skills_browser_support::{installed_row, notice, pin, press, registry, search};
use crate::support::stub;

const FILE: &str = "/repo/.vibe/skills/lint/SKILL.md";

fn local(name: &str, path: &str) -> Value {
    let mut value = pin(name, "local", "project");
    value["path"] = json!(path);
    value
}

fn answer(skills: Vec<Value>, issues: Vec<Value>) -> InstalledSkills {
    serde_json::from_value(json!({"skills": skills, "issues": issues})).unwrap()
}

fn issue(file: &str) -> Value {
    json!({"file": file, "message": "Failed to load: description is too long"})
}

fn opened_with(answer: InstalledSkills) -> vibe_rs::app::App {
    let mut app = vibe_rs::app::App::default();
    skills_browser::apply_event(&mut app, &stub(), Event::Opened(Ok(answer)));
    app
}

#[test]
fn skills_that_failed_to_load_are_listed_under_the_others_and_never_highlighted() {
    let broken = "/repo/.vibe/skills/too-long/SKILL.md";
    let mut app = opened_with(answer(vec![local("lint", FILE)], vec![issue(broken)]));
    let rows = app.skills_browser.rows().to_vec();
    assert_eq!(
        rows,
        [
            Row::InstalledColumns,
            installed_row("lint"),
            Row::BrokenHeading,
            Row::Broken(0),
        ]
    );
    press(&mut app, &stub(), KeyCode::Char('j'));
    assert_eq!(
        app.skills_browser.list.selected(),
        1,
        "only the skill takes the highlight"
    );
}

#[test]
fn a_search_keeps_the_failures_it_matches() {
    let client = stub();
    let issues = vec![issue("/a/too-long/SKILL.md"), issue("/a/broken/SKILL.md")];
    let mut app = opened_with(answer(vec![local("lint", FILE)], issues));
    press(&mut app, &client, KeyCode::Char('/'));
    search(&mut app, &client, "broken");
    let rows = app.skills_browser.rows().to_vec();
    assert_eq!(rows, [Row::BrokenHeading, Row::Broken(1)]);
}

#[test]
fn only_failures_still_open_the_list() {
    let app = opened_with(answer(Vec::new(), vec![issue("/a/x/SKILL.md")]));
    let rows = app.skills_browser.rows().to_vec();
    assert_eq!(rows, [Row::BrokenHeading, Row::Broken(0)]);
}

#[test]
fn e_opens_a_local_skill_s_file_and_nothing_for_a_shared_one() {
    let client = stub();
    let shared = registry("zed", "global", 2, None);
    let mut app = opened_with(answer(vec![local("lint", FILE), shared], Vec::new()));
    press(&mut app, &client, KeyCode::Char('e'));
    assert_eq!(app.external_editor_file, Some(PathBuf::from(FILE)));
    app.external_editor_file = None;
    press(&mut app, &client, KeyCode::Char('j'));
    press(&mut app, &client, KeyCode::Char('e'));
    assert_eq!(app.external_editor_file, None);
    assert!(!help(&app.skills_browser).contains(&("e", action::EDIT)));
}

#[test]
fn n_closes_the_browser_and_leaves_the_skill_creator_in_the_composer() {
    let mut app = opened_with(answer(vec![local("lint", FILE)], Vec::new()));
    assert!(help(&app.skills_browser).contains(&("n", action::NEW)));
    press(&mut app, &stub(), KeyCode::Char('n'));
    assert!(!app.skills_browser.open);
    let draft = app.chat_input.submitted_text();
    assert!(draft.starts_with("/vibe:skill-creator "), "{draft}");
}

#[test]
fn n_keeps_a_draft_the_user_is_writing() {
    let mut app = opened_with(answer(vec![local("lint", FILE)], Vec::new()));
    app.chat_input.load_full_text("half a message".into());
    press(&mut app, &stub(), KeyCode::Char('n'));
    assert!(app.skills_browser.open, "the browser stays open");
    assert_eq!(app.chat_input.submitted_text(), "half a message");
    assert_eq!(notice(&app), Some(skills_browser::labels::DRAFT_KEPT));
}

#[test]
fn a_change_a_running_turn_holds_says_when_it_applies() {
    let mut app = opened_with(answer(vec![local("lint", FILE)], Vec::new()));
    app.skills_browser.busy = Some("Disabling lint".to_owned());
    let parked = Event::Changed {
        outcome: Outcome {
            answer: Some(json!({"status": "pending"})),
            error: None,
        },
        installed: Ok(answer(vec![local("lint", FILE)], Vec::new())),
    };
    skills_browser::apply_event(&mut app, &stub(), parked);
    assert_eq!(
        notice(&app),
        Some("Saved; it applies when the current turn ends.")
    );
}

#[tokio::test]
async fn back_from_the_editor_the_page_shows_what_was_saved() {
    let mut app = vibe_rs::app::App::default();
    let (client, mut frames) = crate::skills_browser_support::session(&mut app);
    let mut before = local("lint", FILE);
    before["description"] = json!("Old description");
    before["prompt"] = json!("# old body");
    let event = Event::Opened(Ok(answer(vec![before], Vec::new())));
    skills_browser::apply_event(&mut app, &client, event);
    crate::skills_browser_support::open_page(&mut app, installed_row("lint"));
    skills_browser::reread(&mut app, &client);
    let (method, _) = crate::skills_browser_support::next(&mut frames).await;
    assert_eq!(method, "skills/installed", "the pins are read again");
    let mut after = local("lint", FILE);
    after["description"] = json!("New description");
    after["prompt"] = json!("# new body");
    app.skills_browser.busy = Some("Disabling lint".to_owned());
    let reread = Event::Reread(Ok(answer(vec![after], Vec::new())));
    skills_browser::apply_event(&mut app, &client, reread);
    assert_eq!(
        app.skills_browser.busy.as_deref(),
        Some("Disabling lint"),
        "the change in flight keeps its spinner"
    );
    let page = app
        .skills_browser
        .page
        .as_ref()
        .expect("the page stays open");
    assert_eq!(page.description, "New description");
    let body = crate::skills_browser_support::shown_body(&app);
    assert_eq!(
        body,
        Some(vibe_rs::skills_browser::model::Body::Ready(
            "# new body".to_owned()
        ))
    );
}
