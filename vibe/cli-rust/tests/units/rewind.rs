//! Rewind prompt navigation edges: the last prompt, slash-command echoes and a running turn.

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::config;
use vibe_rs::focus::Focus;
use vibe_rs::input;
use vibe_rs::rewind;
use vibe_rs::server::Client;

struct Harness {
    app: App,
    client: Arc<Client>,
    frames: mpsc::UnboundedReceiver<Value>,
    events: mpsc::Receiver<rewind::Event>,
    config_tx: mpsc::Sender<config::Loaded>,
}

fn user_message(id: &str, text: &str) -> Value {
    json!({"entry": {
        "id": id,
        "type": "message",
        "role": "user",
        "content": [{"type": "text", "text": text}],
    }})
}

/// An idle session whose transcript holds `hello` (u0) and `world` (u1),
/// optionally with a `/model` command echo between them.
fn harness(command_echo: bool) -> Harness {
    let (client, frames) = Client::stub_answering();
    let (tx, events) = mpsc::channel(8);
    let (config_tx, _config_rx) = mpsc::channel(1);
    let mut app = App::default();
    app.set_session_id("session".to_owned());
    app.session.status = Status::Ready;
    app.rewind.tx = Some(tx);
    app.view.transcript.add(&user_message("u0", "hello"));
    if command_echo {
        // The client-only echo `commands::dispatch::run_command` mounts for `/model`.
        app.view.transcript.add(&json!({"entry": {
            "id": "echo",
            "type": "message",
            "role": "command",
            "content": [{"type": "text", "text": "/model"}],
            "generationStatus": "completed",
            "local": true,
        }}));
    }
    app.view.transcript.add(&user_message("u1", "world"));
    Harness {
        app,
        client: Arc::new(client),
        frames,
        events,
        config_tx,
    }
}

impl Harness {
    fn press(&mut self, code: KeyCode) {
        let key = KeyEvent::new(code, KeyModifiers::NONE);
        input::handle_key(&mut self.app, &self.client, &self.config_tx, key);
    }

    fn rewind_key(&mut self, code: KeyCode) {
        let key = KeyEvent::new(code, KeyModifiers::NONE);
        rewind::handle_key(&mut self.app, &self.client, key);
    }

    fn pending(&self) -> u32 {
        self.app.pending_commits.load(Ordering::Relaxed)
    }

    /// Receive the `session/rewind/read` the last key sent and apply its answer;
    /// returns the requested entry id.
    async fn answer_read(&mut self) -> String {
        let frame = tokio::time::timeout(Duration::from_secs(5), self.frames.recv())
            .await
            .expect("a rewind read is sent")
            .expect("the stub forwards frames");
        assert_eq!(frame["method"], "session/rewind/read");
        assert_eq!(frame["params"]["sessionId"], "session");
        let event = tokio::time::timeout(Duration::from_secs(5), self.events.recv())
            .await
            .expect("the read is answered")
            .expect("the rewind channel stays open");
        rewind::apply_event(&mut self.app, event);
        frame["params"]["entryId"]
            .as_str()
            .expect("entry id")
            .to_owned()
    }

    async fn enter_rewind(&mut self) {
        self.press(KeyCode::Esc);
        self.press(KeyCode::Esc);
        assert_eq!(self.answer_read().await, "u1");
    }
}

#[tokio::test]
async fn left_and_right_step_through_the_user_prompts() {
    let mut h = harness(false);
    h.enter_rewind().await;
    h.rewind_key(KeyCode::Left);
    assert_eq!(h.answer_read().await, "u0");
    assert_eq!(h.app.rewind.entry_id.as_deref(), Some("u0"));
    h.rewind_key(KeyCode::Right);
    assert_eq!(h.answer_read().await, "u1");
    assert_eq!(h.app.rewind.entry_id.as_deref(), Some("u1"));
    h.rewind_key(KeyCode::Right);
    assert_eq!(h.pending(), 0, "Right on the last prompt reads nothing");
    assert_eq!(h.app.rewind.entry_id.as_deref(), Some("u1"));
}

#[tokio::test]
async fn rewind_skips_slash_command_echoes() {
    let mut h = harness(true);
    let ids: Vec<_> = h
        .app
        .view
        .transcript
        .user_messages()
        .into_iter()
        .map(|(_, id, _)| id)
        .collect();
    assert_eq!(ids, ["u0", "u1"]);
    h.enter_rewind().await;
    h.rewind_key(KeyCode::Left);
    assert_eq!(h.answer_read().await, "u0");
}

#[tokio::test]
async fn rewind_does_not_open_while_a_turn_is_running() {
    let mut h = harness(false);
    h.app.session.status = Status::Generating {
        since: Instant::now(),
    };
    rewind::start(&mut h.app, &h.client);
    assert_eq!(h.pending(), 0, "no rewind read is started");
    assert!(!h.app.rewind.open);
    assert_eq!(h.app.focus(), Focus::Composer);
}
