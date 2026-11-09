//! Restore image-aware prompt queue snapshots from the app server.

use serde_json::Value;

use super::QueueItem;
use crate::app::App;
use crate::server::{ContentBlock, ImageAttachment, PublicTurnQueue, QueuedTurn};
use crate::transcript::local;

/// Replace the local queue with a resumed session's own, paused items included.
pub fn adopt(app: &mut App, queue: Option<&PublicTurnQueue>) {
    app.queue.clear();
    if let Some(queue) = queue {
        apply(app, queue);
    }
}

/// Apply a `turn/queue/updated` queue.
pub fn sync(app: &mut App, queue: &Value) {
    apply(app, &crate::utils::json::read(queue));
}

/// Missing prompts are retained because a promotion removes an item before `turn/started`.
pub(super) fn apply(app: &mut App, queue: &PublicTurnQueue) {
    app.queue.paused = queue.paused;
    for queued_turn in &queue.items {
        restore(app, queued_turn);
    }
}

fn restore(app: &mut App, queued_turn: &QueuedTurn) {
    let queue_item_id = queued_turn.id.as_str();
    if app
        .queue
        .items
        .iter()
        .any(|item| item.queue_item_id.as_deref() == Some(queue_item_id))
        || app.queue.started.iter().any(|id| id == queue_item_id)
    {
        return;
    }
    let mut entries = queued_turn.entries.iter().rev();
    let Some(entry) = entries.find(|entry| entry.role.as_deref() == Some("user")) else {
        return;
    };
    let Some(message_id) = entry.entry_id.as_deref() else {
        return;
    };
    let text = entry
        .content
        .iter()
        .filter_map(|block| match block {
            ContentBlock::Text { text } => Some(text.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("\n\n");
    let images: Vec<_> = entry
        .content
        .iter()
        .filter_map(ImageAttachment::from_session_block)
        .collect();
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
        mentions: None,
        sent: true,
        ever_sent: true,
        revision,
        replacing: false,
    });
}
