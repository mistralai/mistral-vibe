//! Child-session notifications must not rebuild the parent transcript.

use serde_json::json;
use vibe_rs::app::Status;
use vibe_rs::event_handler;
use vibe_rs::server::{notification, Notification};

use crate::support::{message, stub, with_status};

fn parent_message() -> serde_json::Value {
    json!({
        "entry": message("parent-msg", "user", "hello")
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
                "history": [message(entry_id, "user", text)],
            },
        }),
    }
}

#[test]
fn a_child_session_snapshot_does_not_replace_parent_history() {
    let mut app = with_status(Status::Ready);
    app.session.session_id = Some("parent".into());
    app.view.transcript.add(&parent_message());

    assert!(event_handler::apply_notification(
        &mut app,
        &stub(),
        &snapshot("child", "child-msg", "child"),
    ));

    assert!(app.view.transcript.contains("parent-msg"));
    assert!(!app.view.transcript.contains("child-msg"));
}

#[test]
fn a_same_session_snapshot_still_loads() {
    let mut app = with_status(Status::Ready);
    app.session.session_id = Some("parent".into());
    app.view.transcript.add(&parent_message());

    assert!(event_handler::apply_notification(
        &mut app,
        &stub(),
        &snapshot("parent", "parent-next", "next"),
    ));

    assert!(app.view.transcript.contains("parent-next"));
}
