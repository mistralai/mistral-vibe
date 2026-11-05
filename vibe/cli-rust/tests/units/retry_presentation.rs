//! `/retry` replaces the rows a failed or interrupted turn left with the retried reply.

use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::{retry, submission, CommandEvent};
use vibe_rs::event_handler::apply_notification;
use vibe_rs::server::{notification, Client, Notification, PublicSessionState};

use crate::support::{message, messages};

const SESSION: &str = "s1";
const TURN: &str = "turn-1";

struct Harness {
    app: App,
    client: Arc<Client>,
    _frames: mpsc::UnboundedReceiver<Value>,
    _commands: mpsc::Receiver<CommandEvent>,
}

impl Harness {
    fn new() -> Self {
        let mut app = App::default();
        app.session.session_id = Some(SESSION.into());
        app.set_status(Status::Ready);
        let (tx, commands) = mpsc::channel(8);
        app.command_tx = Some(tx);
        let (client, frames) = Client::stub_answering();
        Self {
            app,
            client: Arc::new(client),
            _frames: frames,
            _commands: commands,
        }
    }

    fn notify(&mut self, method: &str, params: Value) {
        let event = Notification {
            method: method.into(),
            params,
        };
        apply_notification(&mut self.app, &self.client, &event);
    }

    fn add(&mut self, entry: Value) {
        self.notify(
            notification::HISTORY_ENTRY_ADDED,
            json!({"sessionId": SESSION, "entry": entry}),
        );
    }

    fn start_turn(&mut self, id: &str) {
        self.notify(
            notification::TURN_STARTED,
            json!({"sessionId": SESSION, "turn": {"id": id, "status": "in_progress"}}),
        );
    }

    fn end_turn(&mut self, id: &str, status: &str) {
        let mut turn = json!({"id": id, "status": status});
        if status == "failed" {
            turn["error"] = json!({"code": "backend_error", "message": "Upstream 502"});
        }
        self.notify(
            notification::TURN_COMPLETED,
            json!({"sessionId": SESSION, "turn": turn}),
        );
    }

    /// The user submitted `hello`; the server started the turn and streamed part of an answer.
    fn streaming_turn(&mut self) {
        self.app.set_status(Status::Generating {
            since: vibe_rs::clock::now(),
        });
        self.start_turn(TURN);
        self.add(in_turn(message("user", "user", "hello")));
        self.add(in_turn(message("partial", "assistant", "Hi the")));
    }

    /// The server settles the cut-off answer as discarded, as it does before ending the turn.
    fn discard_partial(&mut self, cause: &str) {
        self.notify(
            notification::HISTORY_ENTRY_UPDATED,
            json!({"sessionId": SESSION, "turnId": TURN, "entryId": "partial", "patch": [
                {"op": "add", "path": "/outcome", "value": {"type": "discarded", "cause": cause}},
            ]}),
        );
    }

    fn escape(&mut self) {
        submission::interrupt_turn(&mut self.app, &self.client);
    }

    /// `/retry`, then the retried turn's first entry.
    fn retry_and_reply(&mut self, reply: &str) {
        retry::start_retry(&mut self.app, &self.client, "");
        self.start_turn("turn-2");
        self.add(message("reply", "assistant", reply));
    }

    fn shown(&self) -> Vec<String> {
        messages(&self.app)
            .into_iter()
            .map(|(_, text)| text)
            .collect()
    }

    fn interrupt_markers(&self) -> usize {
        self.app
            .view
            .transcript
            .lines()
            .filter(|line| matches!(line.entry, vibe_rs::server::HistoryEntry::Interrupt(_)))
            .count()
    }
}

fn in_turn(mut entry: Value) -> Value {
    entry["turnId"] = json!(TURN);
    entry
}

#[tokio::test]
async fn a_failure_is_replaced_by_the_retried_reply() {
    let mut harness = Harness::new();
    harness.streaming_turn();
    harness.discard_partial("failure");
    harness.end_turn(TURN, "failed");
    assert!(harness.app.session.can_retry);

    harness.retry_and_reply("Hi there.");

    assert_eq!(harness.shown(), ["hello", "Hi there."]);
    assert!(harness.app.session.retry_presentation.is_none());
}

#[tokio::test]
async fn esc_mid_answer_is_replaced_by_the_retried_reply() {
    let mut harness = Harness::new();
    harness.streaming_turn();
    harness.escape();
    assert!(
        !harness.app.session.can_retry,
        "nothing to retry before the turn ends"
    );
    harness.discard_partial("interrupt");
    harness.end_turn(TURN, "interrupted");

    harness.retry_and_reply("Hi there.");

    assert_eq!(harness.shown(), ["hello", "Hi there."]);
    assert_eq!(harness.interrupt_markers(), 0);
}

#[tokio::test]
async fn esc_before_the_turn_starts_is_replaced_by_the_retried_reply() {
    let mut harness = Harness::new();
    harness.app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });
    harness.escape();
    harness.start_turn(TURN);
    harness.add(in_turn(message("user", "user", "hello")));
    harness.end_turn(TURN, "interrupted");
    assert!(harness.app.session.can_retry);

    harness.retry_and_reply("Hi there.");

    assert_eq!(harness.shown(), ["hello", "Hi there."]);
    assert_eq!(harness.interrupt_markers(), 0);
}

#[tokio::test]
async fn an_offer_waits_for_the_user_to_retry() {
    let mut harness = Harness::new();
    harness.streaming_turn();
    harness.discard_partial("failure");
    harness.end_turn(TURN, "failed");

    harness.add(message("late", "assistant", "Late."));

    assert!(harness.app.session.can_retry);
    assert!(harness.shown().contains(&"Hi the".to_owned()));
}

#[tokio::test]
async fn a_new_prompt_cancels_the_offer_and_keeps_the_rows() {
    let mut harness = Harness::new();
    harness.streaming_turn();
    harness.discard_partial("failure");
    harness.end_turn(TURN, "failed");

    harness.add(message("next", "user", "next"));

    assert!(!harness.app.session.can_retry);
    assert!(harness.app.session.retry_presentation.is_none());
    assert_eq!(harness.shown()[..2], ["hello", "Hi the"]);
}

#[tokio::test]
async fn esc_on_a_new_prompt_drops_the_earlier_offer() {
    let mut harness = Harness::new();
    harness.streaming_turn();
    harness.discard_partial("failure");
    harness.end_turn(TURN, "failed");
    harness.app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });

    harness.escape();
    harness.start_turn("turn-2");
    harness.add(message("next", "user", "next"));
    harness.end_turn("turn-2", "interrupted");
    harness.retry_and_reply("Done.");

    assert_eq!(harness.interrupt_markers(), 0);
    assert!(
        harness.shown().contains(&"Hi the".to_owned()),
        "the earlier turn's rows stay"
    );
}

#[tokio::test]
async fn a_rejected_interrupted_prompt_keeps_its_marker_out_of_the_next_retry() {
    let mut harness = Harness::new();
    harness.app.queue.items = vec![crate::support::queued("a", "first")];
    harness.app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });
    harness.escape();
    let rejected = vibe_rs::message_queue::QueueEvent::Rejected {
        message_id: "a".into(),
        error: None,
    };
    vibe_rs::message_queue::apply_event(&mut harness.app, &harness.client, rejected);

    harness.streaming_turn();
    harness.discard_partial("failure");
    harness.end_turn(TURN, "failed");
    harness.retry_and_reply("Hi there.");

    assert_eq!(
        harness.interrupt_markers(),
        1,
        "the rejected prompt's marker stays"
    );
}

#[tokio::test]
async fn a_rejected_queued_prompt_keeps_the_running_turns_marker_tracked() {
    let mut harness = Harness::new();
    harness.streaming_turn();
    harness.app.queue.items = vec![crate::support::queued("b", "second")];
    harness.escape();
    let rejected = vibe_rs::message_queue::QueueEvent::Rejected {
        message_id: "b".into(),
        error: None,
    };
    vibe_rs::message_queue::apply_event(&mut harness.app, &harness.client, rejected);

    harness.discard_partial("interrupt");
    harness.end_turn(TURN, "interrupted");
    harness.retry_and_reply("Hi there.");

    assert_eq!(harness.shown(), ["hello", "Hi there."]);
    assert_eq!(harness.interrupt_markers(), 0);
}

#[tokio::test]
async fn a_second_failure_keeps_the_first_rows_and_adds_its_error() {
    let mut harness = Harness::new();
    harness.streaming_turn();
    harness.discard_partial("failure");
    harness.end_turn(TURN, "failed");
    retry::start_retry(&mut harness.app, &harness.client, "");
    harness.start_turn("turn-2");
    harness.end_turn("turn-2", "failed");
    assert!(harness.app.session.can_retry);

    harness.retry_and_reply("Hi there.");

    assert_eq!(harness.shown(), ["hello", "Hi there."]);
}

#[tokio::test]
async fn retry_with_nothing_to_retry_warns_and_sends_nothing() {
    let mut harness = Harness::new();

    retry::start_retry(&mut harness.app, &harness.client, "");

    assert_eq!(
        messages(&harness.app),
        [(
            "command_warning".to_owned(),
            retry::NOTHING_TO_RETRY.to_owned()
        )]
    );
    assert!(matches!(harness.app.session.status, Status::Ready));
}

#[tokio::test]
async fn a_replaced_row_stays_hidden_across_a_live_snapshot() {
    let mut harness = Harness::new();
    harness.streaming_turn();
    harness.discard_partial("failure");
    harness.end_turn(TURN, "failed");
    harness.retry_and_reply("Hi there.");

    let state: PublicSessionState = serde_json::from_value(json!({
        "eventId": 99,
        "session": {"id": SESSION},
        "history": [
            in_turn(message("user", "user", "hello")),
            harness.app.view.transcript.entry_raw("partial").cloned().unwrap(),
            message("reply", "assistant", "Hi there."),
        ],
    }))
    .unwrap();
    harness.app.view.transcript.load_live_snapshot(&state);

    assert_eq!(harness.shown(), ["hello", "Hi there."]);
}

#[tokio::test]
async fn a_session_switch_drops_the_offer() {
    let mut harness = Harness::new();
    harness.streaming_turn();
    harness.discard_partial("failure");
    harness.end_turn(TURN, "failed");

    harness.app.set_session_id("s2".into());

    assert!(harness.app.session.retry_presentation.is_none());
    assert!(!harness.app.session.can_retry);
}
