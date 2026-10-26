//! Session-artifact labels, latest-first list, count label, bar rendering and
//! the artifacts modal's highlight state.

use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::Terminal;
use serde_json::{json, Value};

use vibe_rs::app::App;
use vibe_rs::artifacts;
use vibe_rs::artifacts_modal;
use vibe_rs::server::PublicSessionArtifact;
use vibe_rs::ui::bottom_bar;

const WIDTH: u16 = 120;
const URL: &str = "https://github.com/mistral-ai/vibe/pull/123";
const T1: &str = "2026-10-18T10:00:00+00:00";
const T2: &str = "2026-10-18T11:00:00+00:00";

fn render_row(app: &mut App, width: u16) -> String {
    let mut terminal = Terminal::new(TestBackend::new(width, 1)).expect("terminal");
    terminal
        .draw(|frame| bottom_bar::draw(app, frame, Rect::new(0, 0, width, 1)))
        .expect("draw");
    let buffer = terminal.backend().buffer();
    (0..width).map(|x| buffer[(x, 0)].symbol()).collect()
}

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
    artifacts::apply_list(&mut app, vec![github("a1", 1, T1), github("a2", 2, T2)]);
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
    artifacts::apply_list(&mut app, vec![github("a1", 1, T1), github("a2", 2, T2)]);
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

#[test]
fn center_x_centers_and_clamps_between_the_edge_segments() {
    let area = Rect::new(0, 0, WIDTH, 1);
    assert_eq!(bottom_bar::center_x(area, 7, 20, 100), Some(56));
    assert_eq!(bottom_bar::center_x(area, 7, 60, 100), Some(60));
    assert_eq!(bottom_bar::center_x(area, 7, 0, 50), Some(43));
    assert_eq!(bottom_bar::center_x(area, 7, 60, 62), None);
    assert_eq!(bottom_bar::center_x(area, 0, 0, 100), None);
    assert_eq!(bottom_bar::center_x(area, 121, 0, 120), None);
}

#[test]
fn the_bar_paints_a_single_artifact_as_a_link_that_opens_its_url() {
    let mut app = App::default();
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (0, 600_000);
    app.session.artifacts = vec![github("a1", 123, T1)];

    let row = render_row(&mut app, WIDTH);

    let link = "PR #123";
    let x = (WIDTH as usize - link.len()) / 2;
    assert_eq!(&row[x..x + link.len()], link);
    let painted = app.view.bottom_bar_link.as_ref().expect("link");
    assert_eq!(painted.area, Rect::new(x as u16, 0, link.len() as u16, 1));
    match &painted.action {
        bottom_bar::BarLinkAction::OpenUrl(url) => assert_eq!(url, URL),
        bottom_bar::BarLinkAction::ListArtifacts => panic!("expected OpenUrl"),
    }
}

#[test]
fn the_bar_paints_a_count_link_that_opens_the_modal_when_several_artifacts() {
    let mut app = App::default();
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (0, 600_000);
    app.session.artifacts = vec![github("a1", 123, T1), github("a2", 124, T2)];

    let row = render_row(&mut app, WIDTH);

    let link = "PRs (2)";
    let x = (WIDTH as usize - link.len()) / 2;
    assert_eq!(&row[x..x + link.len()], link);
    let painted = app.view.bottom_bar_link.as_ref().expect("link");
    assert_eq!(painted.area, Rect::new(x as u16, 0, link.len() as u16, 1));
    assert!(matches!(
        painted.action,
        bottom_bar::BarLinkAction::ListArtifacts
    ));
}

#[test]
fn the_bar_hides_the_link_when_it_does_not_fit() {
    let mut app = App::default();
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (0, 600_000);
    app.session.artifacts = vec![github("a1", 123, T1)];

    let row = render_row(&mut app, 30);

    assert!(!row.contains("PR #123"));
    assert!(app.view.bottom_bar_link.is_none());
}

#[test]
fn the_bar_without_an_artifact_paints_no_link() {
    let mut app = App::default();
    app.session.cwd = Some("/tmp/project".into());

    let row = render_row(&mut app, WIDTH);

    assert!(!row.contains("PR #"));
    assert!(app.view.bottom_bar_link.is_none());
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

#[test]
fn the_bar_link_shows_the_synced_status() {
    let mut app = App::default();
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (0, 600_000);
    app.session.artifacts = vec![artifact(
        "a1",
        "github",
        details_with(json!({ "state": "open", "prStatus": "action_needed" })),
        T1,
    )];

    let row = render_row(&mut app, WIDTH);

    assert!(row.contains("PR #123 (action needed)"));
}
