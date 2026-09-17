//! Client-to-server prompt-queue requests: enqueue, replace and remove.

use std::sync::Arc;
use std::time::Instant;

use serde_json::json;

use super::prompt::{entry, prepare, request};
use super::replacement::replace_group;
use super::{drop_item, QueueEvent, QueueItem};
use crate::app::{App, Status};
use crate::commands::submission::new_message_id;
use crate::server::{method, Client, TurnEnqueueParams, TurnQueueRemoveParams};
use crate::transcript::local;

/// Queue a prompt (Python `QueueController.enqueue_prompt`): mount it as queued
/// straight away, then let the server decide when it becomes a turn.
pub fn enqueue_prompt(app: &mut App, client: &Arc<Client>, text: String) {
    let message_id = new_message_id();
    // Idle with nothing queued: the server promotes this prompt straight away, so
    // render it as an ordinary message rather than flashing the queued styling for
    // the enqueue round-trip (Python `optimistic_start`). Compaction is busy
    // (Python `_agent_task`), so a prompt sent mid-compact stays a queued one.
    let optimistic =
        app.queue.is_empty() && matches!(app.session.status, Status::Ready) && !app.compacting;
    match optimistic {
        true => local::add_message(&mut app.view.transcript, &message_id, "user", &text),
        false => local::add_pending_prompt(&mut app.view.transcript, &message_id, &text),
    }
    let merge_target = (!optimistic)
        .then(|| app.queue.items.first())
        .flatten()
        .map(|item| (item.server_message_id.clone(), item.queue_item_id.clone()));
    let (server_message_id, queue_item_id) = merge_target
        .clone()
        .unwrap_or_else(|| (message_id.clone(), None));
    app.queue.items.push(QueueItem {
        queue_item_id,
        message_id: message_id.clone(),
        server_message_id: server_message_id.clone(),
        text: text.clone(),
        images: Vec::new(),
        replacement_pending: false,
        sent: false,
    });
    match merge_target {
        Some((_, Some(_))) => replace_group(app, client, &server_message_id),
        Some((_, None)) => {}
        None => send(app, client, message_id, text),
    }
}

/// Send the prompts submitted before the session was ready (Python holds
/// dispatch on `_session_ready` and enqueues once it is set).
pub fn flush_pending(app: &mut App, client: &Arc<Client>) {
    let unsent: Vec<(String, String)> = app
        .queue
        .items
        .iter()
        .filter(|item| !item.sent && item.message_id == item.server_message_id)
        .map(|item| (item.message_id.clone(), item.text.clone()))
        .collect();
    for (message_id, text) in unsent {
        send(app, client, message_id, text);
    }
}

/// Rewrite the highlighted prompt (Python `queue/replace`), keeping its queue id
/// and FIFO position. A prompt promoted meanwhile is copied into a new one.
pub fn replace_selected(app: &mut App, client: &Arc<Client>, text: String) {
    let Some(index) = app.queue.selected_position() else {
        enqueue_prompt(app, client, text);
        return;
    };
    let message_id = app.queue.items[index].message_id.clone();
    let Some(queue_item_id) = app.queue.items[index].queue_item_id.clone() else {
        if app.queue.items[index].sent {
            enqueue_prompt(app, client, text);
        } else {
            app.queue.items[index].text = text.clone();
            local::set_text(&mut app.view.transcript, &message_id, &text);
        }
        return;
    };
    let server_message_id = app.queue.items[index].server_message_id.clone();
    if app
        .queue
        .items
        .iter()
        .filter(|item| item.server_message_id == server_message_id)
        .any(|item| item.replacement_pending)
    {
        return;
    }
    let _ = queue_item_id;
    app.queue.items[index].text = text;
    app.queue.items[index].sent = false;
    let updated_text = app.queue.items[index].text.clone();
    let images = app.queue.items[index].images.clone();
    local::set_prompt(
        &mut app.view.transcript,
        &message_id,
        &updated_text,
        &images,
    );
    replace_group(app, client, &server_message_id);
}

/// Release a queue the server paused (Python `QueueController.resume`).
pub fn resume(app: &mut App, client: &Arc<Client>) {
    if !app.queue.paused {
        return;
    }
    app.queue.paused = false;
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let _ = request(
            &client,
            method::TURN_QUEUE_RESUME,
            json!({"sessionId": session_id}),
        )
        .await;
    });
}

/// Remove the highlighted prompt (Python `QueueController.pop_at`).
pub fn remove_selected(app: &mut App, client: &Arc<Client>) {
    if let Some(index) = app.queue.selected_position() {
        remove_at(app, client, index);
    }
}

/// Remove the newest queued prompt, the Ctrl+C step of Python's quit ladder.
pub fn pop_last(app: &mut App, client: &Arc<Client>) -> bool {
    if super::mutation_in_flight(app) {
        return true;
    }
    if app.queue.is_empty() {
        return false;
    }
    remove_at(app, client, app.queue.len() - 1);
    true
}

fn remove_at(app: &mut App, client: &Arc<Client>, index: usize) {
    let item = drop_item(app, index);
    app.view.transcript.remove(&item.message_id);
    let (Some(session_id), Some(queue_item_id)) =
        (app.session.session_id.clone(), item.queue_item_id)
    else {
        return;
    };
    if app
        .queue
        .items
        .iter()
        .any(|remaining| remaining.server_message_id == item.server_message_id)
    {
        for remaining in &mut app.queue.items {
            if remaining.server_message_id == item.server_message_id {
                remaining.sent = false;
            }
        }
        replace_group(app, client, &item.server_message_id);
        return;
    }
    let client = client.clone();
    tokio::spawn(async move {
        let params = TurnQueueRemoveParams {
            session_id,
            queue_item_id,
        };
        let _ = request(&client, method::TURN_QUEUE_REMOVE, params).await;
    });
}

/// Prepare the prompt then enqueue it, as Python's `_send_prompt` does before
/// handing the turn to the app server.
fn send(app: &mut App, client: &Arc<Client>, message_id: String, text: String) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    if matches!(app.session.status, Status::Starting | Status::Failed) {
        return;
    }
    // A user-submitted turn re-opens the incomplete-stream budget (`_handle_turn`).
    app.session.incomplete_stream_retries = 0;
    if let Some(item) = app.queue.position(&message_id) {
        app.queue.items[item].sent = true;
    }
    // An optimistically started prompt shows its indicator at once; a queued one
    // leaves the status alone until `turn/started`. Mid-compaction prompts are
    // queued server-side and dropped by the compact response, never started here.
    if matches!(app.session.status, Status::Ready) && !app.compacting {
        app.set_status(Status::Generating {
            since: Instant::now(),
        });
    }
    let tx = app.queue.tx.clone();
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let event = match prepare(&client, &session_id, &text).await {
            Ok(prepared) => {
                let images = prepared.images.clone();
                let params = TurnEnqueueParams {
                    idempotency_key: message_id.clone(),
                    session_id: session_id.clone(),
                    entries: vec![entry(message_id.clone(), &prepared, &text)],
                };
                match serde_json::to_value(params) {
                    Ok(value) => match client.request(method::TURN_ENQUEUE, value).await {
                        Ok(accepted) => match accepted
                            .get("queueItemId")
                            .and_then(|value| value.as_str())
                            .map(str::to_owned)
                        {
                            Some(queue_item_id) => QueueEvent::Accepted {
                                message_id,
                                queue_item_id,
                                session_id,
                                images,
                            },
                            None => QueueEvent::Rejected {
                                message_id,
                                error: None,
                            },
                        },
                        Err(error) => QueueEvent::Rejected {
                            message_id,
                            error: Some(error.to_string()),
                        },
                    },
                    Err(error) => QueueEvent::Rejected {
                        message_id,
                        error: Some(error.to_string()),
                    },
                }
            }
            Err(error) => QueueEvent::Rejected {
                message_id,
                error: Some(error),
            },
        };
        crate::input::deliver(tx, event, &pending).await;
    });
}
