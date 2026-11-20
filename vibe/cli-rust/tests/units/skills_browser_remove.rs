//! `/skills` removal: `r` unpins a shared skill at once, `x` asks before deleting a local skill's folder, `a` adds a shared one.

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::hints::action;
use vibe_rs::skills_browser::{self, help, labels, Event, Outcome};

use crate::skills_browser_support::{
    catalog_row, highlight, installed_row, notice, open_page, opened, pin, pins, press, registry,
    session, shadowed, shared, through,
};
use crate::support::{render, rows, stub};

const FILE: &str = "/repo/.vibe/skills/deploy/SKILL.md";

fn local() -> serde_json::Value {
    let mut value = pin("deploy", "local", "project");
    value["path"] = json!(FILE);
    value
}

fn answered(error: Option<&str>) -> Event {
    Event::Changed {
        outcome: Outcome {
            answer: error.is_none().then(|| json!({})),
            error: error.map(str::to_owned),
        },
        installed: Ok(pins(Vec::new()).into()),
    }
}

fn methods(seen: &[(String, serde_json::Value)]) -> Vec<&str> {
    seen.iter().map(|(method, _)| method.as_str()).collect()
}

#[tokio::test]
async fn r_removes_a_shared_skill_from_every_scope_at_once_and_says_how_to_get_it_back() {
    let both = vec![
        registry("deploy", "project", 3, None),
        shadowed(registry("deploy", "global", 2, None)),
    ];
    let mut app = opened(both, None);
    let (client, mut frames) = session(&mut app);
    assert!(help(&app.skills_browser).contains(&("r", action::REMOVE)));
    press(&mut app, &client, KeyCode::Char('r'));
    let seen = through(&mut frames, "skills/installed").await;
    let scopes: Vec<_> = seen
        .iter()
        .filter(|(method, _)| method == "skills/remove")
        .map(|(_, params)| params["scope"].as_str().unwrap_or_default().to_owned())
        .collect();
    assert_eq!(scopes, ["global", "project"]);
    assert_eq!(notice(&app), None, "nothing is said before the answer");
    skills_browser::apply_event(&mut app, &client, answered(None));
    assert!(notice(&app).is_some_and(|text| text.contains("Add it again from Shared")));
}

#[tokio::test]
async fn a_failed_removal_never_says_the_skill_was_removed() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], None);
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Char('r'));
    through(&mut frames, "skills/installed").await;
    skills_browser::apply_event(&mut app, &client, answered(Some("denied")));
    assert!(notice(&app).is_some_and(|text| !text.contains("Removed deploy")));
}

#[tokio::test]
async fn r_on_a_shared_skill_s_page_removes_it_too() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    open_page(&mut app, installed_row("deploy"));
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Char('r'));
    let seen = through(&mut frames, "skills/installed").await;
    assert_eq!(methods(&seen), ["skills/remove", "skills/installed"]);
}

#[test]
fn a_local_skill_offers_delete_not_remove() {
    let app = opened(vec![local()], None);
    let footer = help(&app.skills_browser);
    assert!(footer.contains(&("x", action::DELETE)));
    assert!(!footer.iter().any(|(key, _)| *key == "r"));
    let plain = opened(vec![pin("lint", "local", "global")], None);
    let footer = help(&plain.skills_browser);
    assert!(
        !footer.iter().any(|(key, _)| *key == "x"),
        "a file the server did not name"
    );
}

#[tokio::test]
async fn x_asks_before_deleting_and_only_delete_confirms() {
    let mut app = opened(vec![local()], None);
    let (client, mut frames) = session(&mut app);
    press(&mut app, &client, KeyCode::Char('x'));
    let confirm = app.skills_browser.confirm.clone().unwrap();
    assert!(!confirm.delete_focused, "Cancel is focused first");
    let footer = help(&app.skills_browser);
    assert_eq!(
        footer.last().map(|(_, action)| *action),
        Some(action::CANCEL)
    );
    press(&mut app, &client, KeyCode::Char('d'));
    press(&mut app, &client, KeyCode::Enter);
    assert!(
        app.skills_browser.confirm.is_none(),
        "Enter on Cancel closes it"
    );
    press(&mut app, &client, KeyCode::Char('x'));
    press(&mut app, &client, KeyCode::Esc);
    assert!(app.skills_browser.confirm.is_none(), "Esc cancels");
    assert!(app.skills_browser.open, "and keeps the browser");
    press(&mut app, &client, KeyCode::Char('x'));
    press(&mut app, &client, KeyCode::Right);
    press(&mut app, &client, KeyCode::Enter);
    let seen = through(&mut frames, "skills/installed").await;
    let deletes: Vec<_> = seen
        .iter()
        .filter(|(method, _)| method == "skills/deleteLocal")
        .map(|(_, params)| params["path"].clone())
        .collect();
    assert_eq!(deletes, [json!(FILE)]);
}

#[test]
fn a_delete_refused_while_busy_keeps_the_confirmation_open() {
    let mut app = opened(vec![local()], None);
    let client = stub();
    press(&mut app, &client, KeyCode::Char('x'));
    app.skills_browser.busy = Some("Disabling lint".to_owned());
    press(&mut app, &client, KeyCode::Right);
    press(&mut app, &client, KeyCode::Enter);
    assert_eq!(notice(&app), Some(labels::BUSY));
    assert!(
        app.skills_browser.confirm.is_some(),
        "Delete can be retried"
    );
    assert_eq!(app.skills_browser.busy.as_deref(), Some("Disabling lint"));
}

#[test]
fn the_confirmation_names_the_folder_it_deletes() {
    let mut app = opened(vec![local()], None);
    press(&mut app, &stub(), KeyCode::Char('x'));
    let screen = rows(&render(100, 30, |frame| app.draw(frame)));
    let text = screen.join("\n");
    assert!(text.contains("Delete the skill deploy?"), "{text}");
    assert!(text.contains("/repo/.vibe/skills/deploy and every file in it"));
    assert!(text.contains("Cancel") && text.contains("Delete"));
}

#[test]
fn the_confirmation_of_a_linked_folder_says_only_the_link_goes() {
    let mut linked = local();
    linked["linked"] = json!(true);
    let mut app = opened(vec![linked], None);
    press(&mut app, &stub(), KeyCode::Char('x'));
    let screen = rows(&render(120, 30, |frame| app.draw(frame)));
    let text = screen.join("\n");
    assert!(
        text.contains("removes the link /repo/.vibe/skills/deploy"),
        "{text}"
    );
    assert!(!text.contains("every file in it"));
}

#[tokio::test]
async fn a_adds_a_shared_skill_from_the_shared_tab() {
    let mut app = opened(Vec::new(), Some(shared()));
    let (client, mut frames) = session(&mut app);
    highlight(&mut app, &catalog_row("id-zeta"));
    let footer = help(&app.skills_browser);
    assert!(footer.contains(&("a", action::ADD)));
    press(&mut app, &client, KeyCode::Char('a'));
    let seen = through(&mut frames, "skills/installed").await;
    let imported: Vec<_> = seen
        .iter()
        .filter(|(method, _)| method == "skills/import")
        .map(|(_, params)| (params["skillId"].clone(), params["alias"].clone()))
        .collect();
    assert_eq!(imported, [(json!("id-zeta"), json!("latest"))]);
}

#[test]
fn a_shared_skill_already_added_offers_no_add() {
    let mut app = opened(vec![registry("deploy", "global", 2, None)], Some(shared()));
    highlight(&mut app, &catalog_row("id-deploy"));
    assert!(!help(&app.skills_browser).iter().any(|(key, _)| *key == "a"));
}
