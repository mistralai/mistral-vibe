//! Child-session notifications must not rebuild the parent transcript.

use std::sync::Arc;

use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::event_handler;
use vibe_rs::server::{notification, Client, Notification};

fn parent_message() -> serde_json::Value {
    json!({
        "entry": {
            "id": "parent-msg",
            "type": "message",
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
            "generationStatus": "completed",
        }
    })
}

fn snapshot(session_id: &str, entry_id: &str, text: &str) -> Notification {
    Notification {
        method: notification::SESSION_SNAPSHOT.to_owned(),
        params: json!({
            "sessionId": session_id,
            "state": {
                "eventId": 5,
                "session": {"id": session_id},
                "history": [{
                    "id": entry_id,
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                    "generationStatus": "completed",
                }],
            },
        }),
    }
}

#[test]
fn a_child_session_snapshot_does_not_replace_parent_history() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    app.session.session_id = Some("parent".into());
    app.view.transcript.add(&parent_message());
    let client = Arc::new(Client::stub());

    assert!(event_handler::apply_notification(
        &mut app,
        &client,
        &snapshot("child", "child-msg", "child"),
    ));

    assert!(app.view.transcript.contains("parent-msg"));
    assert!(!app.view.transcript.contains("child-msg"));
}

#[test]
fn a_same_session_snapshot_still_loads() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    app.session.session_id = Some("parent".into());
    app.view.transcript.add(&parent_message());
    let client = Arc::new(Client::stub());

    assert!(event_handler::apply_notification(
        &mut app,
        &client,
        &snapshot("parent", "parent-next", "next"),
    ));

    assert!(app.view.transcript.contains("parent-next"));
}
