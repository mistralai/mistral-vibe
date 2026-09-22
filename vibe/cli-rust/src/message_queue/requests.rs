//! Client-to-server prompt-queue requests: enqueue, replace and remove.

use std::sync::Arc;
use std::time::Instant;

use serde_json::json;

use super::prompt::{entry, prepare, request};
use super::{drop_item, merge_edit_images, replacement, QueueEvent, QueueItem};
use crate::app::{App, Status};
use crate::commands::submission::new_message_id;
use crate::server::{method, Client, ImageAttachment, TurnEnqueueParams, TurnQueueRemoveParams};
use crate::transcript::local;

/// Mount a prompt as queued, then let the server decide when it becomes a turn.
pub fn enqueue_prompt(app: &mut App, client: &Arc<Client>, text: String) {
    enqueue_prompt_with_images(app, client, text, Vec::new());
}

pub(super) fn enqueue_prompt_with_images(
    app: &mut App,
    client: &Arc<Client>,
    text: String,
    images: Vec<ImageAttachment>,
) {
    let message_id = new_message_id();
    // Idle with nothing queued: the server promotes this prompt straight away.
    let optimistic =
        app.queue.is_empty() && matches!(app.session.status, Status::Ready) && !app.compacting;
    if images.is_empty() {
        match optimistic {
            true => local::add_message(&mut app.view.transcript, &message_id, "user", &text),
            false => local::add_pending_prompt(&mut app.view.transcript, &message_id, &text),
        }
    } else {
        local::add_prompt(
            &mut app.view.transcript,
            &message_id,
            &text,
            &images,
            !optimistic,
        );
    }
    let merge_target = (!optimistic)
        .then(|| app.queue.items.first())
        .flatten()
        .filter(|item| {
            !app.queue.is_consumed(&item.server_message_id)
                || app.queue.replacement_in_flight(&item.server_message_id)
        })
        .map(|item| (item.server_message_id.clone(), item.queue_item_id.clone()));
    let (server_message_id, queue_item_id) = merge_target
        .clone()
        .unwrap_or_else(|| (message_id.clone(), None));
    app.queue.items.push(QueueItem {
        queue_item_id,
        message_id: message_id.clone(),
        server_message_id: server_message_id.clone(),
        text: text.clone(),
        images,
        // Unsent until the enqueue or replace containing this content lands.
        sent: false,
        ever_sent: false,
        revision: 0,
        replacing: false,
    });
    app.queue.revise_group(&server_message_id);
    match merge_target {
        Some((_, Some(_))) => replacement::replace_group(app, client, &server_message_id),
        Some((_, None)) => {}
        None => send(app, client, message_id, text),
    }
}

/// Send prompts held until Python's `_session_ready` equivalent is set.
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
        app.queue.revise_group(&item.server_message_id);
        replacement::replace_group(app, client, &item.server_message_id);
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

/// Prepare the prompt then enqueue it, as Python's `_send_prompt` does.
fn send(app: &mut App, client: &Arc<Client>, message_id: String, text: String) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    if matches!(app.session.status, Status::Starting | Status::Failed) {
        return;
    }
    app.session.incomplete_stream_retries = 0;
    let existing_images = if let Some(index) = app.queue.position(&message_id) {
        app.queue.items[index].mark_sent();
        app.queue.items[index].images.clone()
    } else {
        Vec::new()
    };
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
            Ok(mut prepared) => {
                merge_edit_images(&mut prepared, &existing_images, &text);
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
