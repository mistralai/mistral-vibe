//! An interrupt leaves queued prompts paused until an empty Enter resumes them.

use std::sync::Arc;
use std::time::Instant;

use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::submission;
use vibe_rs::event_handler::apply_notification;
use vibe_rs::message_queue::{self as mq, QueueItem};
use vibe_rs::server::{notification, Client, HistoryEntry, MessageContent, Notification};
use vibe_rs::ui_event::UiEvent;

use crate::support::{message, queued, queued_texts, stub};

const SENT_ID: &str = "u-block";
const QUEUED_ID: &str = "queued-2";

/// A running client-driven turn (like `/retry`) with one follow-up queued behind it.
fn busy_app_with_follow_up() -> App {
    let mut app = App::default();
    app.session.session_id = Some("sess".into());
    app.session.active_turn_id = Some("t-retry".into());
    app.session.status = Status::Generating {
        since: Instant::now(),
    };
    app.view
        .transcript
        .add(&json!({"entry": message(SENT_ID, "user", "block queue")}));
    app.view.transcript.add(&json!({"entry": {
        "id": QUEUED_ID, "type": "message", "role": "user",
        "content": [{"type": "text", "text": "queued prompt"}],
        "generationStatus": "completed", "local": true, "pending": true
    }}));
    app.queue.items.push(QueueItem {
        queue_item_id: Some("q-2".into()),
        ..queued(QUEUED_ID, "queued prompt")
    });
    app
}

fn notify(app: &mut App, client: &Arc<Client>, method: &str, params: Value) {
    let event = Notification {
        method: method.to_owned(),
        params,
    };
    apply_notification(app, client, &event);
}

/// The server's answer to the interrupt: the turn ends and the queue is held.
fn server_pauses_after_interrupt(app: &mut App, client: &Arc<Client>) {
    let turn = json!({"id": "t-retry", "sessionId": "sess", "status": "interrupted"});
    notify(
        app,
        client,
        notification::TURN_COMPLETED,
        json!({"sessionId": "sess", "turn": turn}),
    );
    let item = json!({"id": "q-2", "entries": [{
        "role": "user", "entryId": QUEUED_ID,
        "content": [{"type": "text", "text": "queued prompt"}]
    }]});
    notify(
        app,
        client,
        notification::TURN_QUEUE_UPDATED,
        json!({"sessionId": "sess", "queue": {"paused": true, "items": [item]}}),
    );
}

async fn next_frame(frames: &mut mpsc::UnboundedReceiver<Value>, method: &str) -> Value {
    loop {
        let frame = tokio::time::timeout(std::time::Duration::from_secs(5), frames.recv())
            .await
            .expect("no hang")
            .expect("the client keeps writing");
        if frame["method"] == method {
            return frame;
        }
    }
}

fn submit_text(app: &mut App, client: &Arc<Client>, text: &str) -> bool {
    let (config_tx, _config_rx) = mpsc::channel(1);
    app.chat_input.load_full_text(text.to_owned());
    submission::submit(app, client, &config_tx)
}

fn command_errors(app: &App) -> Vec<String> {
    let mut errors = Vec::new();
    for line in app.view.transcript.lines() {
        if let HistoryEntry::Message(message) = line.entry {
            if let (true, Some(MessageContent::Text { text })) =
                (message.role == "command_error", message.content.first())
            {
                errors.push(text.clone());
            }
        }
    }
    errors
}

#[tokio::test]
async fn interrupting_a_client_driven_turn_ends_it_and_leaves_the_follow_up_paused() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let mut app = busy_app_with_follow_up();

    submission::interrupt_turn(&mut app, &client);

    let interrupt = next_frame(&mut frames, "turn/interrupt").await;
    assert_eq!(interrupt["params"]["expectedTurnId"], "t-retry");
    assert!(matches!(app.session.status, Status::Ready));
    server_pauses_after_interrupt(&mut app, &client);
    assert!(app.queue.paused, "the follow-up waits instead of promoting");
    assert_eq!(queued_texts(&app), ["queued prompt"]);
    assert!(app.view.transcript.is_pending(QUEUED_ID));
    assert!(matches!(app.session.status, Status::Ready));
}

#[tokio::test]
async fn an_empty_enter_resumes_the_paused_queue() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let mut app = busy_app_with_follow_up();
    submission::interrupt_turn(&mut app, &client);
    server_pauses_after_interrupt(&mut app, &client);

    assert!(!submit_text(&mut app, &client, ""));

    let resume = next_frame(&mut frames, "session/turn/queue/resume").await;
    assert_eq!(resume["params"]["sessionId"], "sess");
    assert!(!app.queue.paused);
    assert_eq!(
        queued_texts(&app),
        ["queued prompt"],
        "the server promotes it"
    );
}

#[tokio::test]
async fn a_bare_bang_on_a_paused_queue_errors_keeps_the_input_and_does_not_resume() {
    let client = stub();
    let mut app = busy_app_with_follow_up();
    submission::interrupt_turn(&mut app, &client);
    server_pauses_after_interrupt(&mut app, &client);

    assert!(!submit_text(&mut app, &client, "!"));

    assert_eq!(command_errors(&app), ["No command provided after '!'"]);
    assert_eq!(app.chat_input.full_text(), "!", "the input is kept");
    assert!(app.queue.paused, "the queue stays held, no resume is sent");
    assert_eq!(queued_texts(&app), ["queued prompt"]);
}

#[tokio::test]
async fn rewind_skips_the_paused_queued_prompt_and_highlights_the_last_sent_one() {
    let (client, mut frames) = Client::stub_answering();
    let client = Arc::new(client);
    let (tx, mut rx) = mpsc::channel(1);
    let mut app = busy_app_with_follow_up();
    app.events = Some(tx);
    submission::interrupt_turn(&mut app, &client);
    server_pauses_after_interrupt(&mut app, &client);

    vibe_rs::rewind::start(&mut app, &client);

    let read = next_frame(&mut frames, "session/rewind/read").await;
    assert_eq!(read["params"]["entryId"], SENT_ID);
    let selected = tokio::time::timeout(std::time::Duration::from_secs(5), rx.recv()).await;
    let Ok(Some(UiEvent::Rewind(selected))) = selected else {
        panic!("selected");
    };
    vibe_rs::rewind::apply_event(&mut app, selected);
    assert_eq!(app.rewind.entry_id.as_deref(), Some(SENT_ID));
}

#[tokio::test]
async fn a_submit_whose_preparation_fails_leaves_no_busy_spinner() {
    let client = stub();
    let (tx, mut rx) = mpsc::channel(1);
    let mut app = App::default();
    app.session.session_id = Some("sess".into());
    app.session.status = Status::Ready;
    app.events = Some(tx);

    assert!(!submit_text(&mut app, &client, "please abort"));
    assert!(matches!(app.session.status, Status::Generating { .. }));

    let rejected = tokio::time::timeout(std::time::Duration::from_secs(5), rx.recv()).await;
    let Ok(Some(UiEvent::Queue(rejected))) = rejected else {
        panic!("the failed preparation is reported");
    };
    mq::apply_event(&mut app, &client, rejected);

    assert!(
        matches!(app.session.status, Status::Ready),
        "no stuck spinner"
    );
    assert!(app.queue.is_empty());
    assert_eq!(command_errors(&app).len(), 1, "the failure is shown once");
}
