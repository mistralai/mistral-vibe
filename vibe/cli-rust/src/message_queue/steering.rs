//! Atomic transfer of an accepted queue item into the active turn.

use std::sync::Arc;

use super::QueueEvent;
use crate::app::{App, Status};
use crate::server::{method, Client, TurnQueueSteerParams};

pub fn is_active(app: &App) -> bool {
    app.queue.steering.is_some()
}

pub fn mutation_in_flight(app: &App) -> bool {
    app.queue.any_replacement_in_flight() || is_active(app)
}

pub fn defer_prompt(app: &mut App, text: String) -> bool {
    if app.queue.deferred_prompt.is_some() {
        return false;
    }
    app.queue.deferred_prompt = Some(text);
    true
}

/// Send the merged queued block into the active turn without rebuilding it.
pub fn steer_pending(app: &mut App, client: &Arc<Client>) -> bool {
    if app.queue.paused
        || app.queue.selected.is_some()
        || app.queue.steering.is_some()
        || !matches!(app.session.status, Status::Generating { .. })
    {
        return false;
    }
    let (Some(session_id), Some(expected_turn_id), Some(item)) = (
        app.session.session_id.clone(),
        app.session.active_turn_id.clone(),
        app.queue.items.first(),
    ) else {
        return false;
    };
    let Some(queue_item_id) = item.queue_item_id.clone() else {
        return false;
    };
    let server_message_id = item.server_message_id.clone();
    if app.queue.replacement_in_flight(&server_message_id)
        || app.queue.items.iter().any(|item| !item.sent)
    {
        app.queue.steer_after_replace = true;
        if !app.queue.replacement_in_flight(&server_message_id) {
            super::replacement::replace_group(app, client, &server_message_id);
        }
        return true;
    }
    app.queue.steering = Some(queue_item_id.clone());
    let tx = app.queue.tx.clone();
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let params = TurnQueueSteerParams {
            session_id,
            queue_item_id: queue_item_id.clone(),
            expected_turn_id: expected_turn_id.clone(),
        };
        let accepted = match serde_json::to_value(params) {
            Ok(value) => match client.request(method::TURN_QUEUE_STEER, value).await {
                Ok(result)
                    if result.get("queueItemId").and_then(|value| value.as_str())
                        == Some(queue_item_id.as_str())
                        && result.get("turnId").and_then(|value| value.as_str())
                            == Some(expected_turn_id.as_str()) =>
                {
                    Some(true)
                }
                Ok(_) => None,
                Err(error)
                    if error
                        .to_string()
                        .starts_with("session/turn/queue/steer failed: [") =>
                {
                    Some(false)
                }
                Err(_) => None,
            },
            Err(_) => Some(false),
        };
        let event = QueueEvent::SteerSettled {
            queue_item_id,
            accepted,
        };
        crate::input::deliver(tx, event, &pending).await;
    });
    true
}

pub(crate) fn settled(
    app: &mut App,
    client: &Arc<Client>,
    queue_item_id: &str,
    accepted: Option<bool>,
) {
    if app.queue.steering.as_deref() != Some(queue_item_id) || accepted != Some(false) {
        return;
    }
    app.queue.steering = None;
    tracing::warn!(queue_item_id, "queued steer rejected");
    flush_deferred(app, client);
}

/// Finalize the queued widgets only when their accepted steer enters history.
pub fn history_added(app: &mut App, client: &Arc<Client>, params: &serde_json::Value) -> bool {
    let Some(entry) = params.get("entry") else {
        return false;
    };
    if entry.get("type").and_then(|value| value.as_str()) != Some("message")
        || entry.get("role").and_then(|value| value.as_str()) != Some("user")
        || entry.get("source").and_then(|value| value.as_str()) != Some("turn_steer")
    {
        return false;
    }
    let Some(entry_id) = entry.get("id").and_then(|value| value.as_str()) else {
        return false;
    };
    let Some(queue_item_id) = app.queue.steering.as_deref() else {
        return false;
    };
    let Some(item) = app.queue.items.first() else {
        return false;
    };
    if item.queue_item_id.as_deref() != Some(queue_item_id) || item.server_message_id != entry_id {
        return false;
    }
    let server_message_id = item.server_message_id.clone();
    finish(app, client, &server_message_id);
    true
}

pub fn reconcile_snapshot(
    app: &mut App,
    client: &Arc<Client>,
    state: &crate::server::PublicSessionState,
) {
    let Some(queue_item_id) = app.queue.steering.clone() else {
        if let Some(queue) = &state.turn_queue {
            super::sync(app, queue);
        }
        return;
    };
    let server_message_id = app
        .queue
        .items
        .first()
        .map(|item| item.server_message_id.clone());
    let steered = server_message_id.as_deref().is_some_and(|message_id| {
        state.history.as_ref().is_some_and(|history| {
            history.iter().any(|entry| {
                entry.get("id").and_then(|value| value.as_str()) == Some(message_id)
                    && entry.get("role").and_then(|value| value.as_str()) == Some("user")
                    && entry.get("source").and_then(|value| value.as_str()) == Some("turn_steer")
            })
        })
    });
    let still_queued = state.turn_queue.as_ref().is_some_and(|queue| {
        queue
            .get("items")
            .and_then(|value| value.as_array())
            .is_some_and(|items| {
                items.iter().any(|item| {
                    item.get("id").and_then(|value| value.as_str()) == Some(queue_item_id.as_str())
                })
            })
    });
    let started = state.turns.as_ref().is_some_and(|turns| {
        turns.iter().any(|turn| {
            turn.get("queueItemId").and_then(|value| value.as_str()) == Some(queue_item_id.as_str())
        })
    });
    if steered {
        if let Some(server_message_id) = server_message_id {
            finish(app, client, &server_message_id);
        }
    } else if still_queued {
        app.queue.steering = None;
        flush_deferred(app, client);
    } else if started {
        super::turn_started(app, client, &queue_item_id);
    } else {
        discard(app, client, server_message_id.as_deref());
    }
    if let Some(queue) = &state.turn_queue {
        super::sync(app, queue);
    }
}

fn finish(app: &mut App, client: &Arc<Client>, server_message_id: &str) {
    app.queue.steering = None;
    super::events::retire_group(app, server_message_id);
    flush_deferred(app, client);
}

fn discard(app: &mut App, client: &Arc<Client>, server_message_id: Option<&str>) {
    app.queue.steering = None;
    while let Some(index) = server_message_id.and_then(|message_id| {
        app.queue
            .items
            .iter()
            .position(|item| item.server_message_id == message_id)
    }) {
        let item = super::drop_item(app, index);
        app.view.transcript.remove(&item.message_id);
    }
    flush_deferred(app, client);
}

pub(crate) fn turn_started(app: &mut App, queue_item_id: &str) -> bool {
    if app.queue.steering.as_deref() != Some(queue_item_id) {
        return false;
    }
    app.queue.steering = None;
    true
}

pub(crate) fn flush_deferred(app: &mut App, client: &Arc<Client>) {
    if let Some(text) = app.queue.deferred_prompt.take() {
        super::requests::enqueue_prompt(app, client, text);
    }
}
