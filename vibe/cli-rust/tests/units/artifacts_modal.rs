//! The artifacts modal: number/title/state rows, highlight by id, keys, state colors and session switches.

use crossterm::event::KeyCode;
use serde_json::{json, Value};

use vibe_rs::app::{App, Status};
use vibe_rs::artifacts;
use vibe_rs::artifacts_modal;
use vibe_rs::input::{handle_artifacts_modal_key, handle_priority_key};
use vibe_rs::server::PublicSessionArtifact;
use vibe_rs::ui::pr_row;
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
fn rows_show_the_number_title_and_state_only() {
    let mut app = open_with(vec![
        pr(1, 10, json!({})),
        pr(2, 11, checks(&["build", "lint"])),
    ]);
    let lines = screen(&mut app, 30);
    let title = row_of(&lines, "Pull Requests");
    assert_eq!(row_of(&lines, "● #2  Title 2  open"), title + 1);
    assert_eq!(row_of(&lines, "● #1  Title 1  open"), title + 2);
    assert_eq!(row_of(&lines, "Esc/Ctrl+C cancel"), title + 4);
    let shown = |text: &str| lines.iter().any(|line| line.contains(text));
    assert!(!shown("build") && !shown("https://"), "no checks, no URL");
}

#[test]
fn an_overflowing_list_shows_half_the_screen() {
    let list = (1..=30).map(|n| pr(n, 0, json!({}))).collect();
    let mut app = open_with(list);
    let lines = screen(&mut app, 40);
    let title = row_of(&lines, "Pull Requests");
    assert_eq!(row_of(&lines, "Esc/Ctrl+C cancel"), title + 22, "20 rows");
}

#[test]
fn a_narrow_box_shrinks_the_title_and_keeps_the_state() {
    let mut app = open_with(vec![pr(1234567, 10, json!({})), pr(2, 11, json!({}))]);
    let lines = rows(&render(32, 30, |f| app.draw(f)));
    let y = row_of(&lines, "● #1234567");
    assert!(lines[y].contains("Title…"), "{:?}", lines[y]);
    assert!(lines[y].contains("open"), "{:?}", lines[y]);
    assert!(lines[y].ends_with('│'), "{:?}", lines[y]);
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
fn the_dot_and_state_take_the_lifecycle_color() {
    let mut app = open_with(vec![
        pr(
            1,
            10,
            json!({ "state": "open", "prStatus": "action_needed" }),
        ),
        pr(2, 11, json!({ "state": "draft" })),
        pr(3, 12, json!({ "state": "merged" })),
        pr(4, 13, json!({ "state": "closed" })),
    ]);
    press(&mut app, KeyCode::Down);
    let buffer = render(100, 30, |f| app.draw(f));
    let lines = rows(&buffer);
    for (label, word, color) in [
        ("#1", "open", theme::text_success()),
        ("#2", "draft", theme::muted()),
        ("#4", "closed", theme::text_error()),
    ] {
        let y = row_of(&lines, &format!("● {label}  "));
        let line = &lines[y];
        let dot = line.chars().position(|c| c == '●').expect("dot");
        let state = line[..line.find(word).expect("state")].chars().count();
        for x in [dot, state] {
            assert_eq!(buffer[(x as u16, y as u16)].fg, color, "{label}");
        }
    }
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

#[test]
fn each_state_has_its_word_and_color() {
    let cases = [
        ("open", "open", theme::text_success()),
        ("draft", "draft", theme::muted()),
        ("merged", "merged", theme::merged()),
        ("closed", "closed", theme::text_error()),
    ];
    for (wire, word, color) in cases {
        let state = pr_row::state(&pr(1, 10, json!({ "state": wire })));
        assert_eq!(
            (pr_row::word(state), pr_row::color(state)),
            (word, color),
            "{wire}"
        );
    }
}
