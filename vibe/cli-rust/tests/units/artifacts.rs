//! Session-artifact labels, latest-first list, count label and the artifacts modal's highlight state.

use serde_json::{json, Value};

use vibe_rs::app::App;
use vibe_rs::artifacts;
use vibe_rs::artifacts_modal;
use vibe_rs::server::PublicSessionArtifact;

const URL: &str = "https://github.com/mistral-ai/vibe/pull/123";
const T1: &str = "2026-10-18T10:00:00+00:00";
const T2: &str = "2026-10-18T11:00:00+00:00";
const SESSION: &str = "00000000-0000-4000-8000-000000000001";

fn artifact(
    id: &str,
    provider: &str,
    details: Value,
    discovered_at: &str,
) -> PublicSessionArtifact {
    serde_json::from_value(json!({
        "id": id,
        "sessionId": "00000000-0000-4000-8000-000000000001",
        "kind": "pull_request",
        "provider": provider,
        "externalKey": "github.com/mistral-ai/vibe/pull/123",
        "externalUrl": URL,
        "displayTitle": "Fix the thing",
        "discoveredAt": discovered_at,
        "viewedAt": null,
        "details": details,
    }))
    .expect("artifact")
}

fn github(id: &str, number: u64, discovered_at: &str) -> PublicSessionArtifact {
    artifact(
        id,
        "github",
        json!({ "pullRequestNumber": number }),
        discovered_at,
    )
}

fn gitlab(id: &str, iid: u64, discovered_at: &str) -> PublicSessionArtifact {
    artifact(
        id,
        "gitlab",
        json!({ "mergeRequestIid": iid }),
        discovered_at,
    )
}

#[test]
fn label_uses_the_pr_number_for_github() {
    assert_eq!(artifacts::label(&github("a1", 123, T1)), "PR #123");
}

#[test]
fn label_uses_the_mr_iid_for_gitlab() {
    assert_eq!(artifacts::label(&gitlab("a1", 7, T1)), "MR !7");
}

#[test]
fn label_falls_back_to_the_kind_without_a_number() {
    let no_details = artifact("a1", "github", Value::Null, T1);
    assert_eq!(artifacts::label(&no_details), "PR");
    let empty = artifact("a2", "gitlab", json!({}), T1);
    assert_eq!(artifacts::label(&empty), "MR");
}

#[test]
fn upsert_keeps_the_list_latest_first() {
    let mut app = App::default();
    artifacts::upsert(&mut app, github("a1", 1, T1));
    artifacts::upsert(&mut app, github("a2", 2, T2));
    assert_eq!(app.session.artifacts.len(), 2);
    let latest = artifacts::latest(&app).expect("artifact");
    assert_eq!(artifacts::label(latest), "PR #2");
    // An older notification arriving late joins the list without displacing
    // the latest.
    artifacts::upsert(&mut app, github("a3", 3, "2026-10-18T09:00:00+00:00"));
    assert_eq!(app.session.artifacts.len(), 3);
    let latest = artifacts::latest(&app).expect("artifact");
    assert_eq!(artifacts::label(latest), "PR #2");
    assert_eq!(artifacts::label(&app.session.artifacts[2]), "PR #3");
}

#[test]
fn upsert_replaces_an_updated_artifact_by_id() {
    let mut app = App::default();
    artifacts::upsert(&mut app, github("a1", 1, T1));
    let mut updated = github("a1", 1, "2026-10-18T12:00:00+00:00");
    updated.display_title = Some("Renamed".into());
    artifacts::upsert(&mut app, updated);
    assert_eq!(app.session.artifacts.len(), 1);
    assert_eq!(
        app.session.artifacts[0].display_title.as_deref(),
        Some("Renamed")
    );
}

#[test]
fn apply_list_keeps_every_artifact_latest_first() {
    let mut app = App::default();
    app.session.session_id = Some(SESSION.into());
    artifacts::apply_list(
        &mut app,
        SESSION,
        vec![github("a1", 1, T1), github("a2", 2, T2)],
    );
    assert_eq!(app.session.artifacts.len(), 2);
    let latest = artifacts::latest(&app).expect("artifact");
    assert_eq!(artifacts::label(latest), "PR #2");
}

#[test]
fn count_label_names_the_providers_and_the_total() {
    let prs = vec![github("a1", 1, T1), github("a2", 2, T2)];
    assert_eq!(artifacts::count_label(&prs), "PRs (2)");
    let mrs = vec![gitlab("a3", 7, T1), gitlab("a4", 8, T2)];
    assert_eq!(artifacts::count_label(&mrs), "MRs (2)");
    let mixed = vec![github("a1", 1, T1), gitlab("a3", 7, T2)];
    assert_eq!(artifacts::count_label(&mixed), "PRs/MRs (2)");
}

#[test]
fn modal_title_names_the_providers_present() {
    let prs = vec![github("a1", 1, T1)];
    assert_eq!(artifacts::modal_title(&prs), "Pull Requests");
    let mrs = vec![gitlab("a3", 7, T1)];
    assert_eq!(artifacts::modal_title(&mrs), "Merge Requests");
    let mixed = vec![github("a1", 1, T1), gitlab("a3", 7, T2)];
    assert_eq!(
        artifacts::modal_title(&mixed),
        "Pull Requests / Merge Requests"
    );
    assert_eq!(artifacts::modal_title(&[]), "Artifacts");
}

#[test]
fn the_modal_opens_on_the_latest_and_navigates_wrapping() {
    let mut app = App::default();
    app.session.session_id = Some(SESSION.into());
    artifacts::apply_list(
        &mut app,
        SESSION,
        vec![github("a1", 1, T1), github("a2", 2, T2)],
    );
    artifacts_modal::open(&mut app);
    assert!(app.artifacts_modal.open);
    let selected = artifacts_modal::selected(&app).expect("selected");
    assert_eq!(artifacts::label(selected), "PR #2");
    artifacts_modal::navigate(&mut app, true);
    let selected = artifacts_modal::selected(&app).expect("selected");
    assert_eq!(artifacts::label(selected), "PR #1");
    // Past the end it wraps back to the latest.
    artifacts_modal::navigate(&mut app, true);
    let selected = artifacts_modal::selected(&app).expect("selected");
    assert_eq!(artifacts::label(selected), "PR #2");
    artifacts_modal::close(&mut app);
    assert!(!app.artifacts_modal.open);
}

// --- Phase 4: synced status and CI checks ---

fn details_with(extra: Value) -> Value {
    let mut details = json!({ "pullRequestNumber": 123 });
    let obj = details.as_object_mut().expect("object");
    if let Value::Object(extra) = extra {
        for (key, value) in extra {
            obj.insert(key, value);
        }
    }
    details
}

#[test]
fn the_status_label_reflects_the_lifecycle_state() {
    for (state, expected) in [
        ("merged", "merged"),
        ("closed", "closed"),
        ("draft", "draft"),
    ] {
        let artifact = artifact("a1", "github", details_with(json!({ "state": state })), T1);
        assert_eq!(
            artifacts::status_label(&artifact),
            Some(expected.to_owned()),
            "state {state}"
        );
    }
}

#[test]
fn the_status_label_reflects_the_synced_actionability_status() {
    for (pr_status, expected) in [
        ("action_needed", "action needed"),
        ("waiting", "waiting"),
        ("mergeable", "mergeable"),
    ] {
        let artifact = artifact(
            "a1",
            "github",
            details_with(json!({ "state": "open", "prStatus": pr_status })),
            T1,
        );
        assert_eq!(
            artifacts::status_label(&artifact),
            Some(expected.to_owned()),
            "prStatus {pr_status}"
        );
    }
}

#[test]
fn an_open_pr_without_a_synced_status_shows_none() {
    let artifact = artifact("a1", "github", details_with(json!({ "state": "open" })), T1);
    assert_eq!(artifacts::status_label(&artifact), None);
    assert_eq!(artifacts::labeled(&artifact), "PR #123");
}

#[test]
fn the_short_label_is_the_bare_number_and_falls_back_to_the_label() {
    assert_eq!(artifacts::short_label(&github("a1", 123, T1)), "#123");
    assert_eq!(artifacts::short_label(&gitlab("a2", 7, T1)), "!7");
    let bare = artifact("a3", "github", json!({}), T1);
    assert_eq!(artifacts::short_label(&bare), "PR");
}

#[test]
fn the_standing_reads_the_wire_state_and_status() {
    use artifacts::Standing::*;
    for (details, expected) in [
        (
            json!({ "state": "merged", "prStatus": "action_needed" }),
            Merged,
        ),
        (
            json!({ "state": "closed", "prStatus": "mergeable" }),
            Blocked,
        ),
        (
            json!({ "state": "open", "prStatus": "action_needed" }),
            Blocked,
        ),
        (json!({ "prStatus": "action_needed" }), Blocked),
        (json!({ "state": "open", "prStatus": "mergeable" }), Ready),
        (
            json!({ "state": "draft", "prStatus": "mergeable" }),
            Pending,
        ),
        (json!({ "state": "open", "prStatus": "waiting" }), Pending),
        (json!({ "state": "open" }), Pending),
    ] {
        let artifact = artifact("a1", "github", details_with(details.clone()), T1);
        assert_eq!(artifacts::standing(&artifact), expected, "{details}");
    }
}

#[test]
fn the_bar_label_carries_the_status() {
    let open = artifact(
        "a1",
        "github",
        details_with(json!({ "state": "open", "prStatus": "mergeable" })),
        T1,
    );
    assert_eq!(artifacts::labeled(&open), "PR #123 (mergeable)");

    let merged = artifact(
        "a2",
        "gitlab",
        details_with(json!({ "state": "merged", "mergeRequestIid": 7 })),
        T2,
    );
    assert_eq!(artifacts::labeled(&merged), "MR !7 (merged)");
}
