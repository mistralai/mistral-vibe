//! A session handoff moves the client onto the replacement session and the work it owns.

use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::{clear, compact};
use vibe_rs::message_queue::QueueItem;
use vibe_rs::server::{notification, Notification, PublicSessionState};
use vibe_rs::sessions_page::actions;
use vibe_rs::{event_handler, rewind};

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
fn a_handoff_without_state_falls_back_to_the_session_id_field() {
    let mut app = with_status(Status::Ready);
    app.session.session_id = Some("old-session".into());
    app.subagents.seed_snapshot(handoff_state().child_sessions);
    let bare = Notification {
        method: notification::SESSION_COMPACTED.to_owned(),
        params: json!({"sessionId": "new-session", "oldSessionId": "old-session"}),
    };

    assert!(event_handler::apply_notification(&mut app, &stub(), &bare));

    assert_eq!(app.session.session_id.as_deref(), Some("new-session"));
    assert!(app.subagents.sessions.is_empty());
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

/// A replacement session that owns a running process, a loop and a subagent.
fn handoff_json() -> serde_json::Value {
    json!({
        "eventId": 5,
        "session": {"id": "new-session", "loops": [{
            "id": "loop", "prompt": "check", "intervalSeconds": 60, "nextFireAt": 1.0e12,
        }]},
        "history": [],
        "childSessions": [{"id": "child", "name": "child", "agentType": "generic",
            "status": {"type": "running"}, "createdAt": 1, "updatedAt": 1}],
        "backgroundProcesses": [{"processId": "dev", "command": "make run",
            "status": "running", "createdAt": "now"}],
    })
}

fn handoff_state() -> PublicSessionState {
    serde_json::from_value(handoff_json()).unwrap()
}

fn handoff_notification(app: &mut App, method: &str) {
    let params = json!({
        "sessionId": "new-session",
        "oldSessionId": "old-session",
        "state": handoff_json(),
    });
    let notification = Notification {
        method: method.to_owned(),
        params,
    };
    assert!(event_handler::apply_notification(
        app,
        &stub(),
        &notification
    ));
}

/// One way the client can move onto another session.
type Handoff = fn(&mut App);

#[test]
fn every_session_handoff_adopts_the_new_sessions_background_work() {
    let handoffs: [(&str, Handoff); 6] = [
        ("compacted", |app| {
            handoff_notification(app, notification::SESSION_COMPACTED)
        }),
        ("context cleared", |app| {
            handoff_notification(app, notification::SESSION_CONTEXT_CLEARED)
        }),
        ("manual compact", |app| {
            compact::apply_manual_compacted(app, handoff_state(), "status")
        }),
        ("clear", |app| {
            clear::apply_cleared(app, &handoff_state());
        }),
        ("rewind", |app| {
            let done = rewind::Event::Done {
                entry_id: "entry".into(),
                message: "again".into(),
                restore_errors: Vec::new(),
                old_session_id: "old-session".into(),
                inplace: true,
                state: Box::new(handoff_state()),
            };
            rewind::apply_event(app, done);
        }),
        ("resume", |app| {
            // The Sessions page opened a saved session: `session/resume` answered.
            app.sessions_page.busy = Some("new-session".into());
            let ticket = app.sessions_page.ticket;
            let opened = actions::Event::Opened(ticket, Ok(Box::new(handoff_state())));
            actions::apply_event(app, &stub(), opened);
        }),
    ];
    let mut dropped = Vec::new();
    for (name, handoff) in handoffs {
        let mut app = with_status(Status::Ready);
        app.session.session_id = Some("old-session".into());
        handoff(&mut app);
        assert!(app.is_session("new-session"), "{name}");
        let adopted = (
            app.session.background_tasks.len(),
            app.session.loops.len(),
            app.subagents.sessions.len(),
        );
        if adopted != (1, 1, 1) {
            dropped.push(format!("{name}: {adopted:?}"));
        }
    }
    assert!(dropped.is_empty(), "{dropped:#?}");
}
