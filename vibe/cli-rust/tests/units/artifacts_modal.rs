//! The artifacts modal: fixed-height checks layout, highlight by id, keys, state dot and session switches.

use crossterm::event::KeyCode;
use serde_json::{json, Value};

use vibe_rs::app::{App, Status};
use vibe_rs::artifacts;
use vibe_rs::artifacts_modal;
use vibe_rs::input::{handle_artifacts_modal_key, handle_priority_key};
use vibe_rs::server::PublicSessionArtifact;
use vibe_rs::ui::theme;

use crate::support::{ctrl, key, render, rows, stub};

const SESSION: &str = "00000000-0000-4000-8000-000000000001";
const OTHER: &str = "00000000-0000-4000-8000-000000000003";

fn pr(number: u64, hour: u8, extra: Value) -> PublicSessionArtifact {
    let mut details = json!({ "pullRequestNumber": number });
    if let (Some(details), Value::Object(extra)) = (details.as_object_mut(), extra) {
        details.extend(extra);
    }
    serde_json::from_value(json!({
        "id": format!("a{number}"),
        "sessionId": SESSION,
        "kind": "pull_request",
        "provider": "github",
        "externalKey": format!("github.com/o/r/pull/{number}"),
        "externalUrl": format!("https://github.com/o/r/pull/{number}"),
        "displayTitle": format!("Title {number}"),
        "discoveredAt": format!("2026-10-18T{hour:02}:00:00+00:00"),
        "details": details,
    }))
    .expect("artifact")
}

fn checks(names: &[&str]) -> Value {
    let checks: Vec<Value> = names
        .iter()
        .map(|name| json!({ "name": name, "status": "pass" }))
        .collect();
    json!({ "checks": { "state": "pass", "checks": checks } })
}

fn open_with(list: Vec<PublicSessionArtifact>) -> App {
    let mut app = App::default();
    app.set_status(Status::Ready);
    app.session.session_id = Some(SESSION.into());
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (0, 600_000);
    artifacts::apply_list(&mut app, SESSION, list);
    artifacts_modal::open(&mut app);
    app
}

fn screen(app: &mut App, height: u16) -> Vec<String> {
    rows(&render(100, height, |f| app.draw(f)))
}

fn row_of(lines: &[String], text: &str) -> usize {
    lines
        .iter()
        .position(|line| line.contains(text))
        .unwrap_or_else(|| panic!("{text} not on screen"))
}

fn press(app: &mut App, code: KeyCode) {
    handle_artifacts_modal_key(app, key(code));
}

fn selected_id(app: &App) -> Option<String> {
    artifacts_modal::selected(app).map(|artifact| artifact.id.clone())
}

#[test]
fn checks_sit_under_the_rows_in_order() {
    let mut app = open_with(vec![
        pr(1, 10, json!({})),
        pr(2, 11, checks(&["build", "lint", "tests", "e2e", "deploy"])),
    ]);
    let lines = screen(&mut app, 30);
    let title = row_of(&lines, "Pull Requests");
    let older = row_of(&lines, "PR #1");
    assert_eq!(row_of(&lines, "PR #2"), title + 1);
    assert_eq!(older, title + 2);
    assert_eq!(row_of(&lines, "✓ build"), older + 1);
    assert_eq!(row_of(&lines, "✓ lint"), older + 2);
    assert_eq!(row_of(&lines, "✓ tests"), older + 3);
    assert_eq!(row_of(&lines, "2 more passed"), older + 4);
    assert_eq!(row_of(&lines, "https://github.com/o/r/pull/2"), older + 5);
}

#[test]
fn moving_the_highlight_keeps_the_box_height() {
    let mut app = open_with(vec![pr(1, 10, json!({})), pr(2, 11, checks(&["build"]))]);
    let before = screen(&mut app, 30);
    press(&mut app, KeyCode::Down);
    let after = screen(&mut app, 30);
    let title = row_of(&before, "Pull Requests");
    assert_eq!(row_of(&after, "Pull Requests"), title);
    assert!(!after.iter().any(|line| line.contains("✓ build")));
    assert_eq!(row_of(&after, "https://github.com/o/r/pull/1"), title + 4);
}

#[test]
fn a_short_box_shows_checks_only_once_every_row_fits() {
    let names = ["build", "lint", "tests"];
    let list = (1..=3)
        .map(|n| pr(n, 10 + n as u8, checks(&names)))
        .collect();
    let mut app = open_with(list);
    let mut outcomes = Vec::new();
    for height in 10..=20 {
        let lines = screen(&mut app, height);
        let shown = |text: &str| lines.iter().any(|line| line.contains(text));
        assert!(
            shown("Pull Requests") && shown("Esc/Ctrl+C cancel"),
            "height {height}"
        );
        let rows = ["PR #1", "PR #2", "PR #3"].iter().all(|row| shown(row));
        let checks = shown("✓ build");
        assert!(rows || !checks, "height {height}: a check line hides a row");
        outcomes.push((rows, checks));
    }
    assert!(outcomes.contains(&(true, false)), "{outcomes:?}");
    assert!(outcomes.contains(&(true, true)), "{outcomes:?}");
}

#[test]
fn an_overflowing_list_keeps_the_checks_of_the_highlighted_one() {
    let mut list: Vec<_> = (1..=29).map(|n| pr(n, 0, json!({}))).collect();
    list.push(pr(30, 23, checks(&["build", "lint", "tests"])));
    let mut app = open_with(list);
    let lines = screen(&mut app, 40);
    let title = row_of(&lines, "Pull Requests");
    let build = row_of(&lines, "✓ build");
    assert_eq!(
        build,
        title + 21,
        "20 rows, half the screen, then the checks"
    );
    assert_eq!(row_of(&lines, "✓ tests"), build + 2);
    assert_eq!(row_of(&lines, "https://github.com/o/r/pull/30"), build + 3);
}

#[test]
fn a_narrow_box_cuts_the_label_and_status_at_its_edge() {
    let status = json!({ "state": "open", "prStatus": "action_needed" });
    let mut app = open_with(vec![pr(1234567, 10, status), pr(2, 11, json!({}))]);
    let lines = rows(&render(26, 30, |f| app.draw(f)));
    let y = row_of(&lines, "● PR #12");
    assert!(lines[y].ends_with('│'), "{:?}", lines[y]);
    assert!(lines[y].contains('…'), "{:?}", lines[y]);
}

#[test]
fn a_live_artifact_keeps_the_highlighted_one() {
    let mut app = open_with(vec![pr(1, 10, json!({})), pr(2, 11, json!({}))]);
    press(&mut app, KeyCode::Down);
    assert_eq!(selected_id(&app).as_deref(), Some("a1"));
    artifacts::upsert(&mut app, pr(3, 12, json!({})));
    assert_eq!(selected_id(&app).as_deref(), Some("a1"));
    artifacts::upsert(&mut app, pr(2, 13, json!({ "state": "merged" })));
    assert_eq!(selected_id(&app).as_deref(), Some("a1"));
}

#[test]
fn page_and_edge_keys_clamp() {
    let list = (1..=6).map(|n| pr(n, 10 + n as u8, json!({}))).collect();
    let mut app = open_with(list);
    screen(&mut app, 10);
    let page = usize::from(app.artifacts_modal.list_area.height);
    assert!((1..5).contains(&page), "page {page}");
    press(&mut app, KeyCode::End);
    assert_eq!(app.artifacts_modal.list.selected(), 5);
    press(&mut app, KeyCode::PageDown);
    assert_eq!(app.artifacts_modal.list.selected(), 5);
    press(&mut app, KeyCode::PageUp);
    assert_eq!(app.artifacts_modal.list.selected(), 5 - page);
    press(&mut app, KeyCode::Home);
    assert_eq!(app.artifacts_modal.list.selected(), 0);
    press(&mut app, KeyCode::PageUp);
    assert_eq!(app.artifacts_modal.list.selected(), 0);
    press(&mut app, KeyCode::PageDown);
    assert_eq!(app.artifacts_modal.list.selected(), page);
}

#[test]
fn ctrl_c_closes_the_modal_without_arming_quit_or_clearing_the_hidden_draft() {
    let mut app = open_with(vec![pr(1, 10, json!({})), pr(2, 11, json!({}))]);
    app.chat_input.input = "my long unsent draft".into();
    let exit = handle_priority_key(&mut app, &stub(), ctrl('c'));
    assert_eq!(exit, Some(false));
    assert!(!app.artifacts_modal.open);
    assert!(app.quit.active().is_none());
    assert_eq!(app.chat_input.full_text(), "my long unsent draft");
}

#[test]
fn ctrl_c_closes_the_modal_over_a_viewed_subagent_without_arming_quit() {
    let mut app = open_with(vec![pr(1, 10, json!({})), pr(2, 11, json!({}))]);
    app.subagents.viewed_subagent_id = Some("child".into());
    let exit = handle_priority_key(&mut app, &stub(), ctrl('c'));
    assert_eq!(exit, Some(false));
    assert!(!app.artifacts_modal.open);
    assert!(app.quit.active().is_none());
}

#[test]
fn rows_lead_with_a_state_dot_in_the_footer_colors() {
    let mut app = open_with(vec![
        pr(
            1,
            10,
            json!({ "state": "open", "prStatus": "action_needed" }),
        ),
        pr(2, 11, json!({ "state": "open", "prStatus": "mergeable" })),
        pr(3, 12, json!({ "state": "merged" })),
    ]);
    press(&mut app, KeyCode::Down);
    let buffer = render(100, 30, |f| app.draw(f));
    let lines = rows(&buffer);
    for (label, color) in [
        ("PR #3", theme::text_accent()),
        ("PR #1", theme::text_error()),
    ] {
        let y = row_of(&lines, label);
        let x = lines[y].chars().position(|c| c == '●').expect("dot");
        assert!(lines[y].contains(&format!("● {label}")), "{label}");
        let dot = &buffer[(x as u16, y as u16)];
        assert_eq!((dot.symbol(), dot.fg), ("●", color), "{label}");
    }
    let y = row_of(&lines, "PR #2");
    assert!(!lines[y].contains("(mergeable)"));
    assert!(lines[y].contains("PR #2 mergeable"));
}

#[test]
fn a_read_for_another_session_is_ignored() {
    let mut app = open_with(vec![pr(1, 10, json!({}))]);
    artifacts::apply_list(&mut app, OTHER, vec![pr(2, 11, json!({}))]);
    assert_eq!(app.session.artifacts.len(), 1);
}

#[tokio::test]
async fn a_session_switch_drops_the_old_artifacts_and_closes_the_modal() {
    let mut app = open_with(vec![pr(1, 10, json!({})), pr(2, 11, json!({}))]);
    app.session.artifacts_session = Some(SESSION.into());
    app.set_session_id(OTHER.into());
    // Before any frame: the next draw never shows the old session's list.
    assert!(app.session.artifacts.is_empty());
    assert!(!app.artifacts_modal.open);
    artifacts::follow_session(&mut app, &stub());
    assert_eq!(app.session.artifacts_session.as_deref(), Some(OTHER));
    // The old session's late answer is dropped; the new session's lands.
    artifacts::apply_list(&mut app, SESSION, vec![pr(1, 10, json!({}))]);
    assert!(app.session.artifacts.is_empty());
    artifacts::apply_list(&mut app, OTHER, vec![pr(3, 12, json!({}))]);
    assert_eq!(app.session.artifacts.len(), 1);
}

#[test]
fn a_switch_away_and_back_reads_the_list_again() {
    let mut app = open_with(vec![pr(1, 10, json!({}))]);
    app.session.artifacts_session = Some(SESSION.into());
    app.set_session_id(OTHER.into());
    app.set_session_id(SESSION.into());
    assert!(app.session.artifacts.is_empty());
    assert_eq!(
        app.session.artifacts_session, None,
        "the next frame re-reads it"
    );
}

#[test]
fn the_same_session_reads_nothing_again() {
    let mut app = open_with(vec![pr(1, 10, json!({}))]);
    app.session.artifacts_session = Some(SESSION.into());
    artifacts::follow_session(&mut app, &stub());
    assert_eq!(app.session.artifacts.len(), 1);
    assert!(app.artifacts_modal.open);
}
