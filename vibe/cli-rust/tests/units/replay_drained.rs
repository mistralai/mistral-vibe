//! Replay drain sentinels release the e2e idle marker per request.

use std::sync::Arc;

use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::event_handler;
use vibe_rs::server::{notification, Client, Notification};
use vibe_rs::telemetry::{self, TelemetryEvent};

fn drained(request_id: u64) -> Notification {
    Notification {
        method: notification::REPLAY_DRAINED.to_owned(),
        params: json!({"requestId": request_id}),
    }
}

#[tokio::test]
async fn a_request_owes_frames_until_its_drain_sentinel_lands() {
    let mut app = App::default();
    app.session.status = Status::Starting;
    let (client, _frames) = Client::stub_answering();
    let client = Arc::new(client);
    assert!(!client.owes_replay_frames());

    client.request("first", json!({})).await.expect("first");
    client.request("second", json!({})).await.expect("second");
    assert!(client.owes_replay_frames());

    assert!(event_handler::apply_notification(
        &mut app,
        &client,
        &drained(1)
    ));
    assert!(client.owes_replay_frames());
    assert!(event_handler::apply_notification(
        &mut app,
        &client,
        &drained(2)
    ));
    assert!(!client.owes_replay_frames());
}

fn event() -> TelemetryEvent {
    TelemetryEvent {
        name: "vibe.test",
        properties: Default::default(),
    }
}

#[tokio::test]
async fn sent_telemetry_owes_frames_before_its_task_runs() {
    let (client, _frames) = Client::stub_answering();
    let client = Arc::new(client);

    telemetry::send(&client, "session", event());

    assert!(client.owes_replay_frames());
}

#[tokio::test]
async fn sent_telemetry_owes_frames_until_its_drain_sentinel_lands() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);

    telemetry::send(&client, "session", event());
    frames.recv().await.expect("telemetry frame");
    assert!(client.owes_replay_frames());

    client.mark_replay_drained(1);
    assert!(!client.owes_replay_frames());
}
