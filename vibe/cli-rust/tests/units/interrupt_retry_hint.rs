//! The interrupt marker suggests `/retry` only while the retry offer covers it.

use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::{retry, submission, CommandEvent};
use vibe_rs::event_handler::apply_notification;
use vibe_rs::server::{notification, Client, HistoryEntry, Notification};

use crate::support::message;

const SESSION: &str = "s1";
const TURN: &str = "turn-1";

struct Harness {
    app: App,
    client: Arc<Client>,
    _frames: mpsc::UnboundedReceiver<Value>,
    _commands: mpsc::Receiver<CommandEvent>,
}

impl Harness {
    /// A turn the user interrupted with Esc, which the server then ended.
    fn interrupted() -> Self {
        let mut app = App::default();
        app.session.session_id = Some(SESSION.into());
        let (tx, commands) = mpsc::channel(8);
        app.command_tx = Some(tx);
        let (client, frames) = Client::stub_answering();
        let mut harness = Self {
            app,
            client: Arc::new(client),
            _frames: frames,
            _commands: commands,
        };
        harness.interrupt(TURN);
        harness
    }

    /// The turn `id` starts, Esc interrupts it, and the server ends it.
    fn interrupt(&mut self, id: &str) {
        self.app.set_status(Status::Generating {
            since: vibe_rs::clock::now(),
        });
        self.notify(
            notification::TURN_STARTED,
            json!({"sessionId": SESSION, "turn": {"id": id, "status": "in_progress"}}),
        );
        submission::interrupt_turn(&mut self.app, &self.client);
        self.notify(
            notification::TURN_COMPLETED,
            json!({"sessionId": SESSION, "turn": {"id": id, "status": "interrupted"}}),
        );
    }

    fn notify(&mut self, method: &str, params: Value) {
        let event = Notification {
            method: method.into(),
            params,
        };
        apply_notification(&mut self.app, &self.client, &event);
    }

    fn marker_hints(&self) -> Vec<bool> {
        self.app
            .view
            .transcript
            .lines()
            .filter_map(|line| match line.entry {
                HistoryEntry::Interrupt(marker) => Some(marker.retryable),
                _ => None,
            })
            .collect()
    }
}

#[tokio::test]
async fn an_interrupted_turn_offers_retry_on_its_marker() {
    let harness = Harness::interrupted();

    assert_eq!(harness.marker_hints(), [true]);
}

#[tokio::test]
async fn a_new_prompt_takes_the_retry_hint_off_the_marker() {
    let mut harness = Harness::interrupted();

    harness.notify(
        notification::HISTORY_ENTRY_ADDED,
        json!({"sessionId": SESSION, "entry": message("next", "user", "next")}),
    );

    assert_eq!(harness.marker_hints(), [false]);
}

#[tokio::test]
async fn sending_retry_takes_the_hint_off_until_the_retry_fails() {
    let mut harness = Harness::interrupted();

    retry::start_retry(&mut harness.app, &harness.client, "");
    let sent = harness.marker_hints();
    // A retry whose turn/start fails re-offers /retry on the same rows.
    retry::offer(&mut harness.app, None, None);

    assert_eq!(sent, [false]);
    assert_eq!(harness.marker_hints(), [true]);
}

#[tokio::test]
async fn a_second_interrupt_moves_the_hint_to_its_own_marker() {
    let mut harness = Harness::interrupted();

    harness.interrupt("turn-2");

    assert_eq!(harness.marker_hints(), [false, true]);
}

#[tokio::test]
async fn interrupting_a_retry_before_it_answers_offers_retry_on_the_new_marker_only() {
    let mut harness = Harness::interrupted();
    retry::start_retry(&mut harness.app, &harness.client, "");

    harness.interrupt("turn-2");

    assert_eq!(harness.marker_hints(), [false, true]);
}
