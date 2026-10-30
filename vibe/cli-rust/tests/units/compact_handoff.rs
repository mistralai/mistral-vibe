//! A compaction handoff moves the client onto the replacement session
//! (`session/compacted`).

use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::compact;
use vibe_rs::event_handler;
use vibe_rs::message_queue::QueueItem;
use vibe_rs::server::{notification, Notification, PublicSessionState};

use crate::support::{message, queued, stub, with_status};

fn compacted(session_id: &str, old_session_id: &str) -> Notification {
    Notification {
        method: notification::SESSION_COMPACTED.to_owned(),
        params: json!({
            "sessionId": session_id,
            "oldSessionId": old_session_id,
            "state": {
                "eventId": 5,
                "session": {"id": session_id},
                "history": [],
            },
        }),
    }
}

#[test]
fn compacted_session_id_falls_back_to_the_session_id_field() {
    let params = json!({"sessionId": "new-session", "oldSessionId": "old-session"});
    assert_eq!(
        compact::compacted_session_id(&params).as_deref(),
        Some("new-session")
    );
}

#[test]
fn the_notification_adopts_the_replacement_session() {
    let mut app = with_status(Status::Ready);
    app.session.session_id = Some("old-session".into());
    app.compacting = true;

    assert!(event_handler::apply_notification(
        &mut app,
        &stub(),
        &compacted("new-session", "old-session"),
    ));

    assert_eq!(app.session.session_id.as_deref(), Some("new-session"));
    assert!(!app.compacting);
}

#[test]
fn a_notification_without_a_handoff_settles_but_keeps_the_session() {
    let mut app = with_status(Status::Ready);
    app.session.session_id = Some("old-session".into());
    app.compacting = true;
    let orphan = Notification {
        method: notification::SESSION_COMPACTED.to_owned(),
        params: json!({}),
    };

    assert!(event_handler::apply_notification(
        &mut app,
        &stub(),
        &orphan
    ));

    assert_eq!(app.session.session_id.as_deref(), Some("old-session"));
    assert!(!app.compacting);
}

#[test]
fn the_manual_response_adopts_the_replacement_session_and_drops_the_queue() {
    let mut app = App::default();
    app.session.session_id = Some("old-session".into());
    app.compacting = true;
    app.session.tokens = (7, 10);
    app.queue.items.push(QueueItem {
        queue_item_id: Some("q1".into()),
        revision: 1,
        ..queued("queued-row", "queued")
    });
    app.view
        .transcript
        .add(&json!({"entry": message("queued-row", "user", "queued")}));
    let state = serde_json::from_value::<PublicSessionState>(json!({
        "eventId": 5,
        "session": {"id": "new-session"},
        "history": [],
    }))
    .unwrap();

    compact::apply_manual_compacted(&mut app, state, "status");

    assert_eq!(app.session.session_id.as_deref(), Some("new-session"));
    assert!(!app.compacting);
    assert_eq!(app.session.tokens, (0, 10));
    assert!(app.queue.items.is_empty());
    assert!(!app.view.transcript.contains("queued-row"));
}
