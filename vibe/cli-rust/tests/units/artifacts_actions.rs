//! PR/MR status causes, the bar's active and unseen artifacts, the modal's check and fix actions.

use crossterm::event::KeyCode;
use ratatui::style::Modifier;
use serde_json::{json, Value};

use vibe_rs::app::{App, Status};
use vibe_rs::artifacts;
use vibe_rs::artifacts_modal;
use vibe_rs::input::handle_artifacts_modal_key;
use vibe_rs::server::PublicSessionArtifact;

use crate::support::{bottom_bar, bottom_bar_row, click, key, notify, render, row, stub};

const SESSION: &str = "00000000-0000-4000-8000-000000000001";

pub(crate) fn pr(number: u64, hour: u8, details: Value) -> PublicSessionArtifact {
    let mut all = json!({ "pullRequestNumber": number, "state": "open" });
    if let (Some(all), Value::Object(details)) = (all.as_object_mut(), details) {
        all.extend(details);
    }
    serde_json::from_value(json!({
        "id": format!("a{number}"),
        "sessionId": SESSION,
        "kind": "pull_request",
        "provider": "github",
        "externalKey": format!("github.com/o/r/pull/{number}"),
        "externalUrl": format!("https://github.com/o/r/pull/{number}"),
        "discoveredAt": format!("2026-10-18T{hour:02}:00:00+00:00"),
        "viewedAt": "2026-10-18T09:00:00+00:00",
        "details": all,
    }))
    .expect("artifact")
}

pub(crate) fn blocked(extra: Value) -> PublicSessionArtifact {
    let mut details = json!({ "prStatus": "action_needed" });
    if let (Some(details), Value::Object(extra)) = (details.as_object_mut(), extra) {
        details.extend(extra);
    }
    pr(7, 10, details)
}

fn failing_checks() -> Value {
    json!({
        "prStatusReason": "checks_failing",
        "checks": { "state": "fail", "checks": [
            { "name": "lint", "status": "pass", "url": "https://ci/lint" },
            { "name": "test", "status": "fail", "url": "https://ci/test" },
            { "name": "build", "status": "fail" },
        ] },
    })
}

pub(crate) fn app_with(list: Vec<PublicSessionArtifact>) -> App {
    let mut app = App::default();
    app.set_status(Status::Ready);
    app.session.session_id = Some(SESSION.into());
    app.session.cwd = Some("/tmp/project".into());
    app.artifacts_modal.enabled = true;
    artifacts::apply_list(&mut app, SESSION, list);
    app
}

#[test]
fn the_status_names_the_server_reason() {
    let label = |details| artifacts::status_label(&pr(1, 10, details));
    let cases = [
        (
            json!({ "prStatus": "action_needed", "prStatusReason": "changes_requested" }),
            "changes requested",
        ),
        (
            json!({ "prStatus": "action_needed", "prStatusReason": "checks_failing" }),
            "CI failing",
        ),
        (
            json!({ "prStatus": "action_needed", "prStatusReason": "conflicts" }),
            "conflicts",
        ),
        (json!({ "prStatus": "action_needed" }), "action needed"),
        (
            json!({ "prStatus": "action_needed", "prStatusReason": "from_a_newer_server" }),
            "action needed",
        ),
        (
            json!({ "prStatus": "waiting", "prStatusReason": "checks_pending" }),
            "CI running",
        ),
        (
            json!({ "prStatus": "waiting", "prStatusReason": "review_required" }),
            "review required",
        ),
        (
            json!({ "prStatus": "waiting", "prStatusReason": "merge_blocked" }),
            "waiting",
        ),
        (json!({ "prStatus": "waiting" }), "waiting"),
        (json!({ "prStatus": "mergeable" }), "mergeable"),
        (
            json!({ "state": "merged", "prStatus": "action_needed" }),
            "merged",
        ),
    ];
    for (details, expected) in cases {
        assert_eq!(
            label(details.clone()).as_deref(),
            Some(expected),
            "{details}"
        );
    }
    assert_eq!(label(json!({})), None);
}

#[test]
fn the_bar_shows_the_open_pr_over_merged_and_closed_ones() {
    let mut app = app_with(vec![
        pr(1, 10, json!({ "state": "merged" })),
        pr(2, 11, json!({ "state": "open", "prStatus": "mergeable" })),
        pr(3, 12, json!({ "state": "closed" })),
    ]);
    let row = bottom_bar_row(&mut app, 120);
    assert!(row.contains("● PR #2 mergeable"), "{row}");
    assert!(!row.contains("PRs (3)"));
}

#[test]
fn the_bar_falls_back_to_every_pr_once_none_is_active() {
    let mut app = app_with(vec![
        pr(1, 10, json!({ "state": "merged" })),
        pr(2, 11, json!({ "state": "closed" })),
    ]);
    assert!(bottom_bar_row(&mut app, 120).contains("PRs (2)"));
}

#[tokio::test]
async fn a_status_change_marks_the_link_until_the_modal_opens() {
    let mut app = app_with(vec![pr(1, 10, json!({ "prStatus": "waiting" }))]);
    let label_style = |app: &mut App| {
        let buffer = bottom_bar(app, 120);
        let x = row(&buffer, 0)
            .chars()
            .position(|c| c == 'P')
            .expect("label") as u16;
        buffer[(x, 0)].style()
    };
    assert!(!label_style(&mut app).add_modifier.contains(Modifier::BOLD));

    artifacts::upsert(&mut app, pr(1, 10, json!({ "prStatus": "waiting" })));
    assert!(app.session.artifacts_unseen.is_empty(), "same status");
    artifacts::upsert(&mut app, pr(1, 10, json!({ "prStatus": "mergeable" })));
    assert!(label_style(&mut app).add_modifier.contains(Modifier::BOLD));

    artifacts_modal::show(&mut app, &stub());
    assert!(app.artifacts_modal.open);
    assert!(!label_style(&mut app).add_modifier.contains(Modifier::BOLD));
    // A change landing while the modal shows it is already seen.
    artifacts::upsert(&mut app, pr(1, 10, json!({ "prStatus": "action_needed" })));
    assert!(app.session.artifacts_unseen.is_empty());
}

#[test]
fn a_never_viewed_pr_is_unseen() {
    let mut fresh = pr(1, 10, json!({}));
    fresh.viewed_at = None;
    let app = app_with(vec![fresh]);
    assert!(artifacts::is_unseen(&app, &app.session.artifacts[0]));
}

#[test]
fn showing_an_empty_list_leaves_the_modal_closed() {
    let mut app = app_with(vec![]);
    artifacts_modal::show(&mut app, &stub());
    assert!(!app.artifacts_modal.open);
}

#[test]
fn the_check_link_is_the_first_failing_check_with_a_url() {
    assert_eq!(
        artifacts_modal::check_url(&blocked(failing_checks())),
        Some("https://ci/test")
    );
    assert_eq!(artifacts_modal::check_url(&pr(1, 10, json!({}))), None);
    let green = json!({ "checks": { "state": "pass", "checks": [
        { "name": "lint", "status": "pass", "url": "https://ci/lint" },
    ] } });
    assert_eq!(artifacts_modal::check_url(&pr(1, 10, green)), None);
}

#[test]
fn the_fix_prompt_names_the_cause() {
    let url = "PR #7 (https://github.com/o/r/pull/7)";
    let prompt = |extra| artifacts_modal::fix_prompt(&blocked(extra)).expect("prompt");
    assert_eq!(
        prompt(failing_checks()),
        format!("Fix the failing CI checks on {url}: test, build.")
    );
    assert_eq!(
        prompt(json!({ "prStatusReason": "changes_requested" })),
        format!("Address the changes requested in the review of {url}.")
    );
    assert_eq!(
        prompt(json!({ "prStatusReason": "conflicts" })),
        format!("Resolve the merge conflicts on {url}.")
    );
    assert_eq!(
        prompt(json!({})),
        format!("Find out what blocks {url} from merging and fix it.")
    );
    assert_eq!(
        artifacts_modal::fix_prompt(&pr(1, 10, json!({ "prStatus": "mergeable" }))),
        None
    );
}

#[test]
fn f_drafts_the_fix_prompt_after_the_draft_and_closes_the_modal() {
    let mut app = app_with(vec![blocked(json!({ "prStatusReason": "conflicts" }))]);
    app.chat_input.load_full_text("keep this".into());
    artifacts_modal::open(&mut app);
    handle_artifacts_modal_key(&mut app, key(KeyCode::Char('f')));
    assert!(!app.artifacts_modal.open);
    assert_eq!(
        app.chat_input.full_text(),
        "keep this\n\nResolve the merge conflicts on PR #7 (https://github.com/o/r/pull/7)."
    );
}

#[test]
fn f_does_nothing_on_a_pr_that_needs_no_action() {
    let mut app = app_with(vec![pr(1, 10, json!({ "prStatus": "mergeable" }))]);
    artifacts_modal::open(&mut app);
    handle_artifacts_modal_key(&mut app, key(KeyCode::Char('f')));
    assert!(app.artifacts_modal.open);
    assert!(app.chat_input.full_text().is_empty());
}

#[tokio::test]
async fn a_pr_created_while_the_modal_lists_it_is_seen() {
    let mut app = app_with(vec![pr(1, 10, json!({}))]);
    artifacts_modal::open(&mut app);
    let created = json!({
        "id": "a2",
        "sessionId": SESSION,
        "kind": "pull_request",
        "provider": "github",
        "externalKey": "github.com/o/r/pull/2",
        "externalUrl": "https://github.com/o/r/pull/2",
        "discoveredAt": "2026-10-18T11:00:00+00:00",
        "details": { "pullRequestNumber": 2, "state": "open" },
    });
    let params = json!({ "sessionId": SESSION, "artifact": created });
    notify(&mut app, "artifacts/created", params);
    artifacts_modal::close(&mut app);
    let listed = app
        .session
        .artifacts
        .iter()
        .find(|a| a.id == "a2")
        .expect("listed");
    assert!(!artifacts::is_unseen(&app, listed));
}

#[tokio::test]
async fn clicking_the_footer_link_toggles_the_list_even_twice_in_a_row() {
    let mut app = app_with(vec![pr(1, 10, json!({ "prStatus": "mergeable" }))]);
    let link = |app: &mut App| {
        render(100, 30, |f| app.draw(f));
        let link = app.view.bottom_bar_links.last().expect("PR link").area;
        (link.x, link.y)
    };
    let (x, y) = link(&mut app);
    click(&mut app, x, y);
    assert!(app.artifacts_modal.open);
    let (x, y) = link(&mut app);
    click(&mut app, x, y);
    assert!(
        !app.artifacts_modal.open,
        "a quick second click closes the list"
    );
}
