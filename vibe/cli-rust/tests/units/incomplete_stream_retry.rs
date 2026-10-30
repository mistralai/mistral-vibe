//! Silent incomplete-stream continuations: retry budget, queued-prompt guard, and the /retry offer.

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::retry::MAX_INCOMPLETE_STREAM_RETRIES;
use vibe_rs::commands::CommandEvent;
use vibe_rs::event_handler::apply_notification;
use vibe_rs::message_queue::QueueItem;
use vibe_rs::server::{notification, Client, Notification};

use crate::support::{messages, queued};

const RETRY_HINT: &str =
    "Run /retry [additional instructions] to continue the interrupted response.";

struct Harness {
    app: App,
    client: Arc<Client>,
    frames: mpsc::UnboundedReceiver<Value>,
    _commands: mpsc::Receiver<CommandEvent>,
}

impl Harness {
    /// A running turn whose stream is about to end early.
    fn new() -> Self {
        let mut app = App::default();
        app.session.session_id = Some("session".into());
        let (tx, commands) = mpsc::channel(8);
        app.command_tx = Some(tx);
        let (client, frames) = Client::stub_answering();
        let mut harness = Self {
            app,
            client: Arc::new(client),
            frames,
            _commands: commands,
        };
        harness.start_turn();
        harness
    }

    fn start_turn(&mut self) {
        self.app.session.active_turn_id = Some("turn".into());
        self.app.set_status(Status::Generating {
            since: Instant::now(),
        });
    }

    fn truncate_stream(&mut self) {
        let event = Notification {
            method: notification::TURN_COMPLETED.into(),
            params: json!({"sessionId": "session", "turn": {
                "id": "turn", "sessionId": "session", "status": "failed",
                "error": {"code": "incomplete_stream", "message": "Stream ended early"},
            }}),
        };
        apply_notification(&mut self.app, &self.client, &event);
    }

    async fn next_method(&mut self) -> String {
        let frame = tokio::time::timeout(Duration::from_secs(5), self.frames.recv())
            .await
            .expect("a request is sent")
            .expect("the stub answers");
        frame["method"].as_str().unwrap_or_default().to_owned()
    }

    /// Background commands still unsettled: each auto-retry starts one.
    fn unsettled_commands(&self) -> u32 {
        self.app.pending_commits.load(Ordering::Relaxed)
    }

    fn error_rows(&self) -> Vec<String> {
        messages(&self.app)
            .into_iter()
            .filter(|(role, _)| role == "command_error")
            .map(|(_, text)| text)
            .collect()
    }
}

#[tokio::test]
async fn two_silent_continuations_then_the_third_truncation_offers_retry_once() {
    let mut harness = Harness::new();
    for attempt in 1..=MAX_INCOMPLETE_STREAM_RETRIES {
        harness.truncate_stream();
        assert_eq!(harness.next_method().await, "turn/start");
        assert_eq!(harness.app.session.incomplete_stream_retries, attempt);
        assert!(harness.error_rows().is_empty());
        assert!(matches!(
            harness.app.session.status,
            Status::Generating { .. }
        ));
        harness.start_turn();
    }

    harness.truncate_stream();
    assert_eq!(
        harness.unsettled_commands(),
        MAX_INCOMPLETE_STREAM_RETRIES,
        "no third continuation"
    );
    let errors = harness.error_rows();
    assert_eq!(errors.len(), 1);
    assert!(errors[0].contains("Stream ended early"));
    assert!(errors[0].contains(RETRY_HINT));
    assert!(harness.app.session.can_retry);
    assert!(matches!(harness.app.session.status, Status::Ready));
}

#[tokio::test]
async fn a_queued_prompt_is_not_overtaken_by_a_silent_continuation() {
    let mut harness = Harness::new();
    harness.app.queue.items.push(QueueItem {
        queue_item_id: Some("queued-1".into()),
        ..queued("queued-row", "t2")
    });

    harness.truncate_stream();
    assert_eq!(
        harness.unsettled_commands(),
        0,
        "no continuation turn/start"
    );
    assert_eq!(harness.app.session.incomplete_stream_retries, 0);
    let errors = harness.error_rows();
    assert_eq!(errors.len(), 1);
    assert!(errors[0].contains(RETRY_HINT));
    assert!(harness.app.session.can_retry);
    assert_eq!(
        harness.app.queue.items.len(),
        1,
        "the queued prompt stays next"
    );
}
