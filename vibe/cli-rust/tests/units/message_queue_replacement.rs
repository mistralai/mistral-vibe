//! Revisioned prompt-queue replacement and copy-on-write behavior.

use std::sync::Arc;

use serde_json::json;
use vibe_rs::app::{App, ToastSeverity};
use vibe_rs::message_queue::{self, QueueEvent, QueueItem, ReplacementOutcome};
use vibe_rs::server::Client;

fn queued(message_id: &str, text: &str) -> QueueItem {
    QueueItem {
        queue_item_id: Some("queue-1".to_string()),
        message_id: message_id.to_string(),
        server_message_id: message_id.to_string(),
        text: text.to_string(),
        images: Vec::new(),
        sent: true,
        ever_sent: true,
        revision: 1,
        replacing: false,
    }
}

fn replacement(
    server_message_id: &str,
    revision: u64,
    covered: &[(&str, &str)],
    outcome: ReplacementOutcome,
) -> QueueEvent {
    let delivered = covered
        .iter()
        .map(|(message_id, _)| (*message_id).to_string())
        .collect();
    QueueEvent::GroupReplaced {
        server_message_id: server_message_id.to_string(),
        revision,
        covered: covered
            .iter()
            .map(|(id, text)| ((*id).to_string(), (*text).to_string(), Vec::new()))
            .collect(),
        delivered,
        outcome,
    }
}

#[test]
fn replacement_before_enqueue_answer_updates_in_place() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    let mut item = queued("message-1", "original");
    item.queue_item_id = None;
    app.queue.items.push(item);
    app.queue.selected = Some("message-1".to_string());

    message_queue::replace_selected(&mut app, &client, "edited".to_string());

    assert_eq!(app.queue.items.len(), 1);
    assert_eq!(app.queue.items[0].text, "edited");
    assert!(!app.queue.items[0].sent);
}

#[test]
fn consumed_edit_restores_the_draft_when_the_queue_empties() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    app.queue.items.push(queued("message-1", "original"));
    app.queue.selected = Some("message-1".to_string());
    app.queue.editing = true;
    app.queue.draft = "draft".to_string();
    app.chat_input.input = "edited".to_string();

    assert!(message_queue::turn_started(&mut app, &client, "queue-1"));
    message_queue::end_edit(&mut app);

    assert_eq!(app.chat_input.input, "draft");
}

#[test]
fn successful_replacement_commits_the_queued_edit() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    let mut item = queued("message-1", "edited");
    item.sent = false;
    item.replacing = true;
    app.queue.items.push(item);

    message_queue::apply_event(
        &mut app,
        &client,
        replacement(
            "message-1",
            1,
            &[("message-1", "edited")],
            ReplacementOutcome::Replaced,
        ),
    );

    assert!(app.queue.items[0].sent);
    assert!(!app.queue.items[0].replacing);
}

#[test]
fn successful_replacement_settles_an_edit_promoted_while_saving() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    let mut item = queued("message-1", "edited");
    item.sent = false;
    item.replacing = true;
    app.queue.items.push(item);
    app.view.transcript.add(&json!({
        "entry": {
            "id": "message-1",
            "type": "message",
            "role": "user",
            "content": [{"type": "text", "text": "original"}],
            "generationStatus": "completed"
        }
    }));

    assert!(message_queue::turn_started(&mut app, &client, "queue-1"));
    message_queue::apply_event(
        &mut app,
        &client,
        replacement(
            "message-1",
            1,
            &[("message-1", "edited")],
            ReplacementOutcome::Replaced,
        ),
    );

    assert!(app.queue.items.is_empty());
    assert_eq!(app.view.transcript.user_messages()[0].2, "edited");
}

#[test]
fn consumed_replacement_copies_the_edit_without_overwriting_a_survivor() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    app.queue.items.push(queued("message-2", "survivor"));
    let mut edited = queued("message-1", "edited");
    edited.sent = false;
    edited.replacing = true;
    app.queue.items.push(edited);

    message_queue::apply_event(
        &mut app,
        &client,
        replacement(
            "message-1",
            1,
            &[("message-1", "edited")],
            ReplacementOutcome::Consumed,
        ),
    );

    let texts: Vec<_> = app
        .queue
        .items
        .iter()
        .map(|item| item.text.as_str())
        .collect();
    assert_eq!(texts, ["survivor", "edited"]);
}

#[test]
fn consumed_replacement_does_not_restore_a_removed_prompt() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    let mut item = queued("message-1", "edited");
    item.sent = false;
    item.replacing = true;
    app.queue.items.push(item);
    app.queue.selected = Some("message-1".to_string());

    message_queue::remove_selected(&mut app, &client);
    message_queue::apply_event(
        &mut app,
        &client,
        replacement(
            "message-1",
            1,
            &[("message-1", "edited")],
            ReplacementOutcome::Consumed,
        ),
    );

    assert!(app.queue.items.is_empty());
}

#[test]
fn failed_replacement_keeps_the_prompt_and_surfaces_the_error() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    let mut item = queued("message-1", "edited");
    item.sent = false;
    item.replacing = true;
    app.queue.items.push(item);

    message_queue::apply_event(
        &mut app,
        &client,
        replacement(
            "message-1",
            1,
            &[("message-1", "edited")],
            ReplacementOutcome::Failed,
        ),
    );

    assert_eq!(app.queue.items[0].text, "edited");
    assert!(!app.queue.items[0].sent);
    assert!(!app.queue.items[0].replacing);
    assert!(matches!(
        app.overlays.toasts.back().map(|toast| toast.severity),
        Some(ToastSeverity::Error)
    ));
}

#[test]
fn consumed_group_requeues_more_than_the_event_history_limit() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    let covered: Vec<_> = (0..10)
        .map(|index| {
            (
                format!("message-{index}"),
                format!("text-{index}"),
                Vec::new(),
            )
        })
        .collect();
    for (message_id, text, _) in &covered {
        let mut item = queued(message_id, text);
        item.server_message_id = "message-0".to_string();
        item.sent = false;
        item.replacing = true;
        app.queue.items.push(item);
    }

    message_queue::apply_event(
        &mut app,
        &client,
        QueueEvent::GroupReplaced {
            server_message_id: "message-0".to_string(),
            revision: 1,
            delivered: covered
                .iter()
                .map(|(message_id, _, _)| message_id.clone())
                .collect(),
            covered,
            outcome: ReplacementOutcome::Consumed,
        },
    );

    let texts: Vec<_> = app
        .queue
        .items
        .iter()
        .map(|item| item.text.as_str())
        .collect();
    let expected: Vec<_> = (0..10).map(|index| format!("text-{index}")).collect();
    assert_eq!(texts, expected);
}

#[test]
fn an_edit_during_a_replace_is_requeued_once_with_its_latest_text() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    let mut item = queued("message-1", "old");
    item.sent = false;
    item.replacing = true;
    app.queue.items.push(item);
    app.queue.selected = Some("message-1".to_string());

    message_queue::replace_selected(&mut app, &client, "latest".to_string());
    message_queue::apply_event(
        &mut app,
        &client,
        replacement(
            "message-1",
            1,
            &[("message-1", "old")],
            ReplacementOutcome::Consumed,
        ),
    );

    let texts: Vec<_> = app
        .queue
        .items
        .iter()
        .map(|item| item.text.as_str())
        .collect();
    assert_eq!(texts, ["latest"]);
}

#[test]
fn turn_started_requeues_an_unsent_prompt_without_an_in_flight_replace() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    app.queue.items.push(queued("message-1", "original"));
    let mut late = queued("message-2", "late");
    late.server_message_id = "message-1".to_string();
    late.sent = false;
    late.ever_sent = false;
    app.queue.items.push(late);

    assert!(message_queue::turn_started(&mut app, &client, "queue-1"));

    assert_eq!(app.queue.items.len(), 1);
    assert_eq!(app.queue.items[0].text, "late");
    assert_eq!(
        app.queue.items[0].server_message_id,
        app.queue.items[0].message_id
    );
}
