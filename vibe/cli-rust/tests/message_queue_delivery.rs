//! Delivered prompt identity across copy-on-write queue edits.

use std::sync::Arc;

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::message_queue::{self, QueueEvent, QueueItem, ReplacementOutcome};
use vibe_rs::server::{Client, HistoryEntry, MessageContent};

fn queued(message_id: &str, text: &str) -> QueueItem {
    QueueItem {
        queue_item_id: Some("queue-1".to_string()),
        message_id: message_id.to_string(),
        server_message_id: "message-1".to_string(),
        text: text.to_string(),
        images: Vec::new(),
        sent: true,
        ever_sent: true,
        revision: 1,
        replacing: false,
    }
}

fn add_pending_prompt(app: &mut App, message_id: &str, text: &str) {
    app.view.transcript.add(&json!({
        "entry": {
            "id": message_id,
            "type": "message",
            "role": "user",
            "content": [{"type": "text", "text": text}],
            "generationStatus": "completed",
            "local": true,
            "pending": true
        }
    }));
}

fn consumed(delivered: &[&str], text: &str) -> QueueEvent {
    QueueEvent::GroupReplaced {
        server_message_id: "message-1".to_string(),
        revision: 1,
        covered: vec![("message-1".to_string(), text.to_string(), Vec::new())],
        delivered: delivered.iter().map(|id| (*id).to_string()).collect(),
        outcome: ReplacementOutcome::Consumed,
    }
}

fn prompt_text(app: &App, message_id: &str) -> String {
    let entry = app
        .view
        .transcript
        .lines()
        .find(|entry| entry.id == message_id)
        .expect("prompt");
    let HistoryEntry::Message(message) = entry.entry else {
        panic!("prompt is not a message");
    };
    let Some(MessageContent::Text { text }) = message.content.first() else {
        panic!("prompt has no text");
    };
    text.clone()
}

#[test]
fn consumed_edit_keeps_the_delivered_prompt_and_copies_the_new_text() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    app.queue.items.push(queued("message-1", "original"));
    app.queue.selected = Some("message-1".to_string());
    add_pending_prompt(&mut app, "message-1", "original");

    message_queue::replace_selected(&mut app, &client, "edited".to_string());
    message_queue::apply_event(&mut app, &client, consumed(&["message-1"], "edited"));

    assert!(app.view.transcript.contains("message-1"));
    assert_eq!(prompt_text(&app, "message-1"), "original");
    assert!(!app.view.transcript.entry(0).expect("kept").pending);
    assert_eq!(app.queue.items.len(), 1);
    assert_eq!(app.queue.items[0].text, "edited");
    assert_ne!(app.queue.items[0].message_id, "message-1");
}

#[test]
fn consumed_edit_before_enqueue_answer_keeps_the_original_transcript() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    let mut item = queued("message-1", "original");
    item.queue_item_id = None;
    app.queue.items.push(item);
    app.queue.selected = Some("message-1".to_string());
    add_pending_prompt(&mut app, "message-1", "original");

    message_queue::replace_selected(&mut app, &client, "edited".to_string());
    assert_eq!(prompt_text(&app, "message-1"), "original");
    assert!(!message_queue::turn_started(&mut app, &client, "queue-1"));
    message_queue::apply_event(
        &mut app,
        &client,
        QueueEvent::Accepted {
            message_id: "message-1".to_string(),
            queue_item_id: "queue-1".to_string(),
            session_id: "session-1".to_string(),
            images: Vec::new(),
        },
    );

    assert!(app.view.transcript.contains("message-1"));
    assert_eq!(prompt_text(&app, "message-1"), "original");
    assert!(!app.view.transcript.entry(0).expect("kept").pending);
    assert_eq!(app.queue.items[0].text, "edited");
    assert_ne!(app.queue.items[0].message_id, "message-1");
}

#[test]
fn consumed_edit_preserves_the_delivered_group_order() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    for (message_id, text) in [
        ("message-1", "first"),
        ("message-2", "original"),
        ("message-3", "third"),
    ] {
        app.queue.items.push(queued(message_id, text));
        add_pending_prompt(&mut app, message_id, text);
    }
    app.queue.items[1].text = "edited".to_string();
    app.queue.items[1].sent = false;
    app.queue.items[1].replacing = true;

    assert!(message_queue::turn_started(&mut app, &client, "queue-1"));
    message_queue::apply_event(
        &mut app,
        &client,
        QueueEvent::GroupReplaced {
            server_message_id: "message-1".to_string(),
            revision: 1,
            covered: vec![("message-2".to_string(), "edited".to_string(), Vec::new())],
            delivered: ["message-1", "message-2", "message-3"]
                .into_iter()
                .map(str::to_string)
                .collect(),
            outcome: ReplacementOutcome::Consumed,
        },
    );

    let entries = (0..3)
        .map(|index| app.view.transcript.entry(index).expect("delivered prompt"))
        .collect::<Vec<_>>();
    assert_eq!(
        entries.iter().map(|entry| entry.id).collect::<Vec<_>>(),
        ["message-1", "message-2", "message-3"]
    );
    assert!(entries.iter().all(|entry| !entry.pending));
    assert!(!entries[0].follows_user);
    assert!(entries[0].followed_by_user);
    assert!(entries[1].follows_user);
    assert!(entries[1].followed_by_user);
    assert!(entries[2].follows_user);
    assert!(!entries[2].followed_by_user);
}
