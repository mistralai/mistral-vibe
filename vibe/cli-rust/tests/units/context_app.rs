//! `/context` state: a failed read clears a stale breakdown, a stale
//! generation is dropped, a good read replaces the previous split.

use vibe_rs::app::App;
use vibe_rs::context_app::{apply_event, Event};
use vibe_rs::server::{ContextBreakdown, ContextCategory, ContextCategoryId};

fn breakdown() -> ContextBreakdown {
    ContextBreakdown {
        context_window: 200_000,
        compaction_threshold: 150_000,
        used_tokens: 42_000,
        measured: true,
        total_characters: 1_000,
        categories: vec![ContextCategory {
            id: Some(ContextCategoryId::System),
            characters: 1_000,
            estimated_tokens: 42_000,
        }],
    }
}

#[test]
fn a_failed_read_clears_the_previous_breakdown_so_the_error_is_visible() {
    let mut app = App::default();
    app.context_app.generation = 1;
    app.context_app.breakdown = Some(breakdown());
    apply_event(&mut app, 1, Event::Read(Err("boom".to_owned())));
    assert!(app.context_app.breakdown.is_none());
    assert_eq!(
        app.context_app.error.as_deref(),
        Some("Failed to read context usage: boom")
    );
}

#[test]
fn a_successful_read_replaces_the_breakdown_and_clears_the_error() {
    let mut app = App::default();
    app.context_app.generation = 2;
    app.context_app.error = Some("old".to_owned());
    apply_event(&mut app, 2, Event::Read(Ok(breakdown())));
    assert!(app.context_app.breakdown.is_some());
    assert!(app.context_app.error.is_none());
}

#[test]
fn a_stale_generation_is_dropped() {
    let mut app = App::default();
    app.context_app.generation = 5;
    app.context_app.breakdown = Some(breakdown());
    apply_event(&mut app, 4, Event::Read(Err("stale".to_owned())));
    assert!(app.context_app.breakdown.is_some());
    assert!(app.context_app.error.is_none());
}

/// The `session/context/read` params the client sends for `app`'s current view.
async fn read_session_id(app: &mut App) -> String {
    let (client, mut frames) = vibe_rs::server::Client::stub_answering();
    vibe_rs::context_app::open(app, &std::sync::Arc::new(client));
    let frame = frames.recv().await.expect("a context read is sent");
    assert_eq!(frame["method"], "session/context/read");
    frame["params"]["sessionId"]
        .as_str()
        .unwrap_or_default()
        .to_owned()
}

#[tokio::test]
async fn the_read_targets_the_main_session_by_default() {
    let mut app = App::default();
    app.session.session_id = Some("root".to_owned());
    assert_eq!(read_session_id(&mut app).await, "root");
}

#[tokio::test]
async fn the_read_targets_the_viewed_subagent_so_it_matches_the_footer() {
    let mut app = App::default();
    app.session.session_id = Some("root".to_owned());
    app.subagents.viewed_subagent_id = Some("child".to_owned());
    assert_eq!(read_session_id(&mut app).await, "child");
}

#[tokio::test]
async fn an_update_of_the_viewed_subagent_re_reads_its_breakdown() {
    let mut app = App::default();
    app.session.status = vibe_rs::app::Status::Ready;
    app.session.session_id = Some("root".to_owned());
    app.subagents.viewed_subagent_id = Some("child".to_owned());
    app.context_app.open = true;
    let (client, mut frames) = vibe_rs::server::Client::stub_answering();
    let notification = vibe_rs::server::Notification {
        method: "session/childSessionUpdated".into(),
        params: serde_json::json!({"childSession": {
            "id": "child",
            "name": "explore-1",
            "agentType": "explore",
            "status": {"type": "running"},
            "contextUsage": {"inputTokens": 40, "outputTokens": 60, "totalTokens": 100},
            "createdAt": 1,
            "updatedAt": 2,
        }}),
    };
    vibe_rs::event_handler::apply_notification(
        &mut app,
        &std::sync::Arc::new(client),
        &notification,
    );
    let read = loop {
        let frame = frames.recv().await.expect("a context read is sent");
        if frame["method"] == "session/context/read" {
            break frame;
        }
    };
    assert_eq!(read["params"]["sessionId"], "child");
}

/// The gauge's marker row of the `/context` app drawn on a `width`-cell terminal.
fn marker_row(width: u16) -> String {
    let mut app = App::default();
    app.set_status(vibe_rs::app::Status::Ready);
    app.context_app.open = true;
    app.context_app.breakdown = Some(breakdown());
    let buffer = crate::support::render(width, 24, |frame| app.draw(frame));
    crate::support::rows(&buffer)
        .into_iter()
        .find(|row| row.contains("auto-compact") || row.contains('▼'))
        .expect("a marker row")
}

#[test]
fn the_threshold_marker_survives_a_clipped_label() {
    // At 75% of a 26-cell gauge, the full label fits on neither side of the threshold.
    let row = marker_row(30);
    assert!(row.contains('▼'), "{row:?}");
    assert!(!row.contains("auto-compact at 75%"), "{row:?}");
}
