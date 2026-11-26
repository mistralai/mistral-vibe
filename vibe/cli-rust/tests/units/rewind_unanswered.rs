//! An interrupt before any answer takes the prompt back: the turn is rewound and the prompt refills the input.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::server::Client;
use vibe_rs::{config, input, rewind};

use vibe_rs::server::notification;

use crate::support::{key, notify};

struct Harness {
    app: App,
    client: Arc<Client>,
    frames: mpsc::UnboundedReceiver<Value>,
    config_tx: mpsc::Sender<config::Loaded>,
}

fn entry(id: &str, kind: &str, extra: Value) -> Value {
    let mut entry = json!({"id": id, "type": kind});
    entry
        .as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    json!({"entry": entry})
}

fn text(id: &str, role: &str, text: &str) -> Value {
    entry(
        id,
        "message",
        json!({"role": role, "content": [{"type": "text", "text": text}], "turnId": "turn"}),
    )
}

fn earlier(id: &str, role: &str, body: &str) -> Value {
    let mut value = text(id, role, body);
    value["entry"]["turnId"] = json!("turn-0");
    value
}

/// A session generating the turn of `world` (u1), after an answered `hello` (u0).
fn harness() -> Harness {
    let (client, frames) = Client::stub_answering();
    let (tx, _events) = mpsc::channel(8);
    let (config_tx, _config_rx) = mpsc::channel(1);
    let mut app = App::default();
    app.set_session_id("session".to_owned());
    app.session.status = Status::Generating {
        since: vibe_rs::clock::now(),
    };
    app.session.active_turn_id = Some("turn".to_owned());
    app.events = Some(tx);
    app.view.transcript.add(&earlier("u0", "user", "hello"));
    app.view.transcript.add(&earlier("a0", "assistant", "Hi."));
    app.view.transcript.add(&text("u1", "user", "world"));
    Harness {
        app,
        client: Arc::new(client),
        frames,
        config_tx,
    }
}

impl Harness {
    fn press(&mut self, code: KeyCode) {
        input::handle_key(&mut self.app, &self.client, &self.config_tx, key(code));
    }

    async fn next_method(&mut self) -> Value {
        let frame = tokio::time::timeout(Duration::from_secs(5), self.frames.recv())
            .await
            .expect("a request is sent")
            .expect("the stub forwards frames");
        frame
    }

    fn settle(&mut self, status: &str) {
        rewind::turn_settled(&mut self.app, &self.client, Some("turn"), Some(status));
    }

    fn has_interrupt_marker(&self) -> bool {
        self.app
            .view
            .transcript
            .lines()
            .any(|line| matches!(line.entry, vibe_rs::server::HistoryEntry::Interrupt(_)))
    }
}

#[tokio::test]
async fn only_assistant_text_or_a_tool_call_answers_a_prompt() {
    let mut h = harness();
    assert_eq!(
        h.app.view.transcript.unanswered_prompt().as_deref(),
        Some("u1")
    );
    let reasoning = entry("r1", "reasoning", json!({"text": "thinking"}));
    h.app.view.transcript.add(&reasoning);
    h.app.view.transcript.add(&text("e1", "assistant", "  "));
    assert_eq!(
        h.app.view.transcript.unanswered_prompt().as_deref(),
        Some("u1")
    );
    h.app
        .view
        .transcript
        .add(&entry("t1", "effect", json!({"title": "bash"})));
    assert_eq!(h.app.view.transcript.unanswered_prompt(), None);
}

#[tokio::test]
async fn an_interrupt_before_any_answer_rewinds_the_turn_once_it_completes() {
    let mut h = harness();
    h.press(KeyCode::Esc);
    assert_eq!(h.next_method().await["method"], "turn/interrupt");
    assert!(h.app.session.taking_back);
    assert!(!h.has_interrupt_marker());
    h.settle("interrupted");
    let frame = h.next_method().await;
    assert_eq!(frame["method"], "session/rewind");
    assert_eq!(frame["params"]["entryId"], "u1");
    assert_eq!(frame["params"]["inplace"], true);
    assert_eq!(frame["params"]["restoreFiles"], false);
    assert!(!h.app.rewind.open);
    assert_eq!(h.app.rewind.entry_id, None);
}

#[tokio::test]
async fn a_second_escape_does_not_open_the_panel_during_a_take_back() {
    let mut h = harness();
    h.press(KeyCode::Esc);
    h.press(KeyCode::Esc);
    assert_eq!(h.next_method().await["method"], "turn/interrupt");
    assert!(h.frames.try_recv().is_err());
    assert!(!h.app.rewind.open);
}

#[tokio::test]
async fn an_interrupt_after_an_answer_leaves_the_marker() {
    let mut h = harness();
    h.app.view.transcript.add(&text("a1", "assistant", "Wor"));
    h.press(KeyCode::Esc);
    assert_eq!(h.next_method().await["method"], "turn/interrupt");
    assert!(!h.app.session.taking_back);
    assert!(h.has_interrupt_marker());
}

#[tokio::test]
async fn an_interrupt_with_a_draft_leaves_the_marker() {
    let mut h = harness();
    h.app.chat_input.load_prompt_text("next idea".to_owned());
    h.press(KeyCode::Esc);
    assert!(!h.app.session.taking_back);
    assert!(h.has_interrupt_marker());
}

#[tokio::test]
async fn an_answer_landing_before_completion_falls_back_to_the_marker() {
    let mut h = harness();
    h.press(KeyCode::Esc);
    assert_eq!(h.next_method().await["method"], "turn/interrupt");
    h.app
        .view
        .transcript
        .add(&entry("t1", "effect", json!({"title": "bash"})));
    h.settle("interrupted");
    assert!(!h.app.session.taking_back);
    assert!(h.has_interrupt_marker());
    assert!(h.frames.try_recv().is_err());
}

#[tokio::test]
async fn a_turn_that_ends_otherwise_than_interrupted_keeps_its_prompt() {
    let mut h = harness();
    h.press(KeyCode::Esc);
    assert_eq!(h.next_method().await["method"], "turn/interrupt");
    h.settle("failed");
    assert!(!h.app.session.taking_back);
    assert!(h.has_interrupt_marker());
    assert!(h.frames.try_recv().is_err());
}

#[tokio::test]
async fn a_steered_turn_with_two_prompts_is_not_taken_back() {
    let mut h = harness();
    h.app.view.transcript.add(&text("u2", "user", "and also"));
    h.press(KeyCode::Esc);
    assert_eq!(h.next_method().await["method"], "turn/interrupt");
    h.settle("interrupted");
    assert!(h.has_interrupt_marker());
    assert!(h.frames.try_recv().is_err());
}

#[tokio::test]
async fn the_input_waits_while_the_take_back_rewinds() {
    let mut h = harness();
    h.press(KeyCode::Esc);
    assert_eq!(h.next_method().await["method"], "turn/interrupt");
    h.settle("interrupted");
    assert_eq!(h.next_method().await["method"], "session/rewind");
    h.app.chat_input.load_prompt_text("too early".to_owned());
    h.press(KeyCode::Enter);
    assert_eq!(h.app.chat_input.full_text(), "too early");
    assert!(h.app.overlays.notice.is_some());
    assert!(h.frames.try_recv().is_err());
}

#[tokio::test]
async fn a_failed_take_back_restores_the_marker_and_the_retry_offer() {
    let mut h = harness();
    h.press(KeyCode::Esc);
    assert_eq!(h.next_method().await["method"], "turn/interrupt");
    rewind::apply_event(&mut h.app, rewind::Event::Failed("busy".into()));
    assert!(!h.app.session.taking_back);
    assert!(h.has_interrupt_marker());
    assert!(h.app.session.can_retry);
}

#[tokio::test]
async fn the_input_still_submits_before_the_interrupted_turn_completes() {
    let mut h = harness();
    h.press(KeyCode::Esc);
    assert_eq!(h.next_method().await["method"], "turn/interrupt");
    h.app.chat_input.load_prompt_text("next idea".to_owned());
    h.press(KeyCode::Enter);
    assert!(h.app.chat_input.full_text().is_empty());
}

#[tokio::test]
async fn another_turn_starting_first_restores_the_marker() {
    let mut h = harness();
    h.press(KeyCode::Esc);
    assert_eq!(h.next_method().await["method"], "turn/interrupt");
    notify(
        &mut h.app,
        notification::TURN_STARTED,
        json!({"turn": {"id": "loop-turn"}}),
    );
    assert!(!h.app.session.taking_back);
    assert!(h.has_interrupt_marker());
}
