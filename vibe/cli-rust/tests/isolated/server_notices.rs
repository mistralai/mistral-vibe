//! Server `warning`/`error`/`turn/retrying` notifications reach the transcript (VIBE-4099).

use std::sync::Arc;

use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::event_handler;
use vibe_rs::server::{notification, Client, HistoryEntry, Notification};

fn notif(method: &str, params: serde_json::Value) -> Notification {
    Notification {
        method: method.to_owned(),
        params,
    }
}

/// Every message role the transcript holds, in order.
fn roles(app: &App) -> Vec<String> {
    app.view
        .transcript
        .lines()
        .filter_map(|entry| match entry.entry {
            HistoryEntry::Message(message) => Some(message.role.clone()),
            _ => None,
        })
        .collect()
}

#[test]
fn a_warning_before_the_first_turn_joins_the_startup_box() {
    let mut app = App::default();
    app.set_status(Status::Ready);
    let client = Arc::new(Client::stub());

    for message in [
        "config is deprecated",
        "Couldn't load connectors GitHub: timed out.",
    ] {
        let event = notif(
            notification::WARNING,
            json!({"warning": {"code": "integrations_unavailable", "message": message}}),
        );
        event_handler::apply_notification(&mut app, &client, &event);
    }

    assert_eq!(roles(&app), vec!["startup_notices"]);
    assert!(app.overlays.notice.is_none());
}

#[test]
fn a_warning_before_ready_is_kept() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());

    let event = notif(
        notification::WARNING,
        json!({"warning": {"code": "warning", "message": "held"}}),
    );
    event_handler::apply_notification(&mut app, &client, &event);

    assert_eq!(roles(&app), vec!["startup_notices"]);
}

#[test]
fn once_a_turn_started_warnings_and_errors_are_rows() {
    let mut app = App::default();
    app.set_status(Status::Ready);
    let client = Arc::new(Client::stub());

    for event in [
        notif(
            notification::TURN_STARTED,
            json!({"sessionId": "s", "turn": {"id": "t1"}}),
        ),
        notif(
            notification::WARNING,
            json!({"warning": {"code": "warning", "message": "first"}}),
        ),
        notif(
            notification::ERROR,
            json!({"error": {"code": "error", "message": "second"}}),
        ),
    ] {
        event_handler::apply_notification(&mut app, &client, &event);
    }

    assert_eq!(roles(&app), vec!["notice_warning", "notice_error"]);
    assert!(app.overlays.notice.is_none());
}

#[test]
fn turn_retrying_notification_is_ignored() {
    // ADR 0009: the compatibility `turn/retrying` notification must not drive the
    // label; retry state derives from `PublicSessionState.retrying` on snapshots.
    let mut app = App::default();
    app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });
    let before = app.view.loading.label().to_owned();
    let client = Arc::new(Client::stub());

    let event = notif(
        notification::TURN_RETRYING,
        json!({"sessionId": "s", "category": "rate_limited", "detail": "429"}),
    );
    event_handler::apply_notification(&mut app, &client, &event);

    assert_eq!(app.view.loading.label(), before);
    assert_ne!(app.view.loading.label(), "Retrying");
}
