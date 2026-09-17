//! Serialized rewrites of the merged server queue item.

use std::sync::Arc;

use super::images::item_images;
use super::prompt::{entry, prepare, request};
use super::{merge_edit_images, QueueEvent};
use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::server::{method, Client, ImageAttachment, PreparedPrompt, TurnQueueReplaceParams};
use crate::transcript::local;

/// Fold all individually rendered busy-time prompts into one server queue item.
pub(super) fn replace_group(app: &mut App, client: &Arc<Client>, server_message_id: &str) {
    if app.queue.replacing.is_some() {
        return;
    }
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let group: Vec<usize> = app
        .queue
        .items
        .iter()
        .enumerate()
        .filter(|(_, item)| item.server_message_id == server_message_id)
        .map(|(index, _)| index)
        .collect();
    let Some(queue_item_id) = group
        .first()
        .and_then(|index| app.queue.items[*index].queue_item_id.clone())
    else {
        return;
    };
    let items = group
        .iter()
        .map(|index| {
            let item = &app.queue.items[*index];
            (
                item.message_id.clone(),
                item.text.clone(),
                item.images.clone(),
            )
        })
        .collect::<Vec<_>>();
    let message_ids = items
        .iter()
        .map(|(message_id, _, _)| message_id.clone())
        .collect();
    for index in group {
        app.queue.items[index].replacement_pending = true;
    }
    let server_message_id = server_message_id.to_owned();
    app.queue.replacing = Some(server_message_id.clone());
    let tx = app.queue.tx.clone();
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let prepared = async {
            let mut prompts = Vec::with_capacity(items.len());
            for (_, text, existing_images) in &items {
                let mut prompt = prepare(&client, &session_id, text).await?;
                merge_edit_images(&mut prompt, existing_images, text);
                prompts.push((text, prompt));
            }
            let text = prompts
                .iter()
                .map(|(fallback, prompt)| prompt.prompt_text.as_ref().unwrap_or(fallback))
                .cloned()
                .collect::<Vec<_>>()
                .join("\n\n");
            let mut merged = PreparedPrompt::from_text(text.clone());
            merged.images = prompts
                .into_iter()
                .flat_map(|(_, prompt)| prompt.images)
                .collect();
            Ok::<_, String>((text, merged))
        }
        .await;
        let (images, error) = match prepared {
            Ok((text, prepared)) => {
                let params = TurnQueueReplaceParams {
                    idempotency_key: new_message_id(),
                    session_id,
                    queue_item_id,
                    entries: vec![entry(server_message_id.clone(), &prepared, &text)],
                };
                match request(&client, method::TURN_QUEUE_REPLACE, params).await {
                    Ok(()) => (prepared.images, None),
                    Err(error) => (Vec::new(), Some(error)),
                }
            }
            Err(error) => (Vec::new(), Some(error)),
        };
        let event = QueueEvent::GroupReplaced {
            server_message_id,
            message_ids,
            images,
            error,
        };
        crate::input::deliver(tx, event, &pending).await;
    });
}

pub(super) fn settled(
    app: &mut App,
    client: &Arc<Client>,
    server_message_id: &str,
    message_ids: &[String],
    images: &[ImageAttachment],
    error: Option<&str>,
) {
    let has_pending_items = app
        .queue
        .items
        .iter()
        .any(|item| item.server_message_id == server_message_id && item.replacement_pending);
    if app.queue.replacing.as_deref() != Some(server_message_id) && !has_pending_items {
        return;
    }
    if app.queue.replacing.as_deref() == Some(server_message_id) {
        app.queue.replacing = None;
    }
    for item in &mut app.queue.items {
        if message_ids.contains(&item.message_id) {
            item.replacement_pending = false;
        }
    }
    let accepted = error.is_none();
    if let Some(error) = error {
        local::add_command_error(
            &mut app.view.transcript,
            &format!("{server_message_id}-error"),
            error,
        );
        app.queue.steer_after_replace = false;
    } else {
        let current_ids: Vec<String> = app
            .queue
            .items
            .iter()
            .filter(|item| item.server_message_id == server_message_id)
            .map(|item| item.message_id.clone())
            .collect();
        if current_ids == message_ids {
            let single_item = message_ids.len() == 1;
            for item in app
                .queue
                .items
                .iter_mut()
                .filter(|item| item.server_message_id == server_message_id)
            {
                let item_images = item_images(item, images, single_item);
                item.images = item_images.clone();
                item.sent = true;
                local::set_prompt(
                    &mut app.view.transcript,
                    &item.message_id,
                    &item.text,
                    &item_images,
                );
            }
        }
    }
    super::steering::flush_deferred(app, client);
    if accepted
        && app.queue.replacing.is_none()
        && std::mem::take(&mut app.queue.steer_after_replace)
    {
        super::steering::steer_pending(app, client);
    }
}

pub(super) fn turn_started(app: &mut App, queue_item_id: &str) -> bool {
    let server_message_id = app
        .queue
        .items
        .iter()
        .find(|item| item.queue_item_id.as_deref() == Some(queue_item_id))
        .map(|item| item.server_message_id.clone());
    let was_replacing = server_message_id
        .as_deref()
        .is_some_and(|message_id| app.queue.replacing.as_deref() == Some(message_id));
    if was_replacing {
        app.queue.replacing = None;
        app.queue.steer_after_replace = false;
        for item in &mut app.queue.items {
            if Some(item.server_message_id.as_str()) == server_message_id.as_deref() {
                item.replacement_pending = false;
            }
        }
    }
    was_replacing
}
