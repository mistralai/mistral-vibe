//! Restore image-aware prompt queue snapshots from the app server.

use serde_json::Value;

use super::QueueItem;
use crate::app::App;
use crate::server::{ContentBlock, ImageAttachment};
use crate::transcript::local;

/// Missing prompts are retained because a promotion removes an item before `turn/started`.
pub fn sync(app: &mut App, queue: &Value) {
    app.queue.paused = queue
        .get("paused")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let Some(items) = queue.get("items").and_then(Value::as_array) else {
        return;
    };
    for queued_turn in items {
        restore(app, queued_turn);
    }
}

fn restore(app: &mut App, queued_turn: &Value) {
    let Some(queue_item_id) = queued_turn.get("id").and_then(Value::as_str) else {
        return;
    };
    if app
        .queue
        .items
        .iter()
        .any(|item| item.queue_item_id.as_deref() == Some(queue_item_id))
        || app.queue.started.iter().any(|id| id == queue_item_id)
    {
        return;
    }
    let Some(entry) = user_entry(queued_turn) else {
        return;
    };
    let Some(message_id) = entry.get("entryId").and_then(Value::as_str) else {
        return;
    };
    let text = entry_text(entry);
    let images = entry_images(entry);
    if let Some(index) = app.queue.position(message_id) {
        let item = &mut app.queue.items[index];
        item.queue_item_id = Some(queue_item_id.to_owned());
        item.images = images.clone();
        if item.sent {
            item.ever_sent = true;
            local::set_prompt(&mut app.view.transcript, message_id, &item.text, &images);
        }
        return;
    }
    local::add_prompt(&mut app.view.transcript, message_id, &text, &images, true);
    let revision = app.queue.revise_group(message_id);
    app.queue.items.push(QueueItem {
        queue_item_id: Some(queue_item_id.to_owned()),
        message_id: message_id.to_owned(),
        server_message_id: message_id.to_owned(),
        text,
        images,
        sent: true,
        ever_sent: true,
        revision,
        replacing: false,
    });
}

fn user_entry(queued_turn: &Value) -> Option<&Value> {
    queued_turn
        .get("entries")?
        .as_array()?
        .iter()
        .rev()
        .find(|entry| entry.get("role").and_then(Value::as_str) == Some("user"))
}

fn entry_text(entry: &Value) -> String {
    entry
        .get("content")
        .and_then(Value::as_array)
        .map(|blocks| {
            blocks
                .iter()
                .filter(|block| block.get("type").and_then(Value::as_str) == Some("text"))
                .filter_map(|block| block.get("text").and_then(Value::as_str))
                .collect::<Vec<_>>()
                .join("\n\n")
        })
        .unwrap_or_default()
}

fn entry_images(entry: &Value) -> Vec<ImageAttachment> {
    entry
        .get("content")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|block| serde_json::from_value::<ContentBlock>(block.clone()).ok())
        .filter_map(|block| ImageAttachment::from_session_block(&block))
        .collect()
}
