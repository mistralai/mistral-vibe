//! A bounded same-session snapshot keeps the retry continuation intact.

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::commands::retry::RetryContinuation;
use vibe_rs::commands::retry_continuation;
use vibe_rs::server::PublicSessionState;

fn entry(id: &str, role: &str, text: &str) -> Value {
    json!({
        "id": id,
        "type": "message",
        "role": role,
        "content": [{"type": "text", "text": text}],
        "generationStatus": "completed",
    })
}

fn add_message(app: &mut App, id: &str, role: &str, text: &str) {
    app.view
        .transcript
        .add(&json!({"entry": entry(id, role, text)}));
}

fn state(history: Vec<Value>, event_id: u64) -> PublicSessionState {
    serde_json::from_value(json!({
        "eventId": event_id,
        "session": {"id": "s1"},
        "history": history,
    }))
    .unwrap()
}

#[test]
fn bounded_page_keeps_the_loaded_prefix_and_the_continuation() {
    let mut app = App::default();
    app.session.session_id = Some("s1".into());
    add_message(&mut app, "user", "user", "hello");
    add_message(&mut app, "dst", "assistant", "Part one.");
    add_message(&mut app, "src", "assistant", " Part two.");
    let base = app.view.transcript.entry_content("dst").unwrap();
    app.view.transcript.hide("src");
    app.view.transcript.merge_continuation("dst", "src", &base);
    app.session.retry_continuation = Some(RetryContinuation {
        dst_id: "dst".into(),
        src_id: "src".into(),
        base_content: base,
    });

    // The page holds only the newer continuation source; the older interrupted
    // answer is off-page and must survive via the already-loaded prefix.
    let page = vec![entry("src", "assistant", " Part two. Part three.")];
    app.view.transcript.load_live_snapshot(&state(page, 9));
    retry_continuation::reconcile_snapshot(&mut app, true);

    assert!(app.view.transcript.contains("dst"));
    assert!(app.view.transcript.contains("src"));
    let merged = app.view.transcript.last_assistant_message().unwrap();
    assert_eq!(merged.trim(), "Part one. Part two. Part three.");
    assert!(app.session.retry_continuation.is_some());
}

#[test]
fn an_unrelated_page_replaces_state_and_drops_the_continuation() {
    let mut app = App::default();
    app.session.session_id = Some("s1".into());
    add_message(&mut app, "user", "user", "hello");
    add_message(&mut app, "dst", "assistant", "Part one.");
    add_message(&mut app, "src", "assistant", " Part two.");
    let base = app.view.transcript.entry_content("dst").unwrap();
    app.session.retry_continuation = Some(RetryContinuation {
        dst_id: "dst".into(),
        src_id: "src".into(),
        base_content: base,
    });

    let page = vec![entry("fresh", "assistant", "Unrelated page.")];
    app.view.transcript.load_live_snapshot(&state(page, 9));
    retry_continuation::reconcile_snapshot(&mut app, true);

    assert!(!app.view.transcript.contains("dst"));
    assert!(!app.view.transcript.contains("src"));
    assert!(app.session.retry_continuation.is_none());
}
