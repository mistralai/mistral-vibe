//! A turn the server starts on its own (a fired loop) is interruptible with Esc.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::event_handler::apply_notification;
use vibe_rs::input::handle_key;
use vibe_rs::server::{notification, Client, Notification};

use crate::support::key;

#[tokio::test]
async fn escape_interrupts_a_turn_the_client_never_submitted() {
    let mut app = App::default();
    app.session.session_id = Some("session".into());
    app.set_status(Status::Ready);
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let (config_tx, _config_rx) = mpsc::channel(1);

    let started = Notification {
        method: notification::TURN_STARTED.into(),
        params: json!({"sessionId": "session", "turn": {
            "id": "scheduled-turn", "sessionId": "session", "status": "in_progress",
        }}),
    };
    apply_notification(&mut app, &client, &started);
    assert!(matches!(app.session.status, Status::Generating { .. }));
    assert!(
        app.queue.items.is_empty(),
        "nothing was submitted by the client"
    );

    assert!(!handle_key(
        &mut app,
        &client,
        &config_tx,
        key(KeyCode::Esc)
    ));
    assert!(matches!(app.session.status, Status::Ready));
    let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("the interrupt is sent")
        .expect("the stub answers");
    assert_eq!(frame["method"], "turn/interrupt");
    assert_eq!(frame["params"]["sessionId"], "session");
    assert_eq!(frame["params"]["expectedTurnId"], "scheduled-turn");
}
