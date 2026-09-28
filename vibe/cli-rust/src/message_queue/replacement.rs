//! Serialized rewrites of the merged server queue item.

use std::sync::Arc;

use super::prompt::{entry, prepare};
use super::{merge_edit_images, QueueEvent, ReplacementOutcome};
use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::server::{method, Client, PreparedPrompt, RequestFailure, TurnQueueReplaceParams};

/// Fold all individually rendered busy-time prompts into one server queue item.
pub(super) fn replace_group(app: &mut App, client: &Arc<Client>, server_message_id: &str) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let Some(queue_item_id) = app
        .queue
        .items
        .iter()
        .find(|item| item.server_message_id == server_message_id)
        .and_then(|item| item.queue_item_id.clone())
    else {
        return;
    };
    let Some(revision) = app.queue.begin_replace(server_message_id) else {
        return;
    };
    let items = app
        .queue
        .items
        .iter()
        .filter(|item| item.server_message_id == server_message_id)
        .map(|item| {
            (
                item.message_id.clone(),
                item.text.clone(),
                item.images.clone(),
                item.sent,
            )
        })
        .collect::<Vec<_>>();
    let members = app
        .queue
        .items
        .iter()
        .filter(|item| item.server_message_id == server_message_id)
        .map(|item| (item.message_id.clone(), item.ever_sent))
        .collect::<Vec<_>>();
    let server_message_id = server_message_id.to_owned();
    let tx = app.queue.tx.clone();
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let prepared = async {
            let mut prompts = Vec::with_capacity(items.len());
            for (message_id, text, existing_images, sent) in items {
                let mut prompt = prepare(&client, &session_id, &text).await?;
                merge_edit_images(&mut prompt, &existing_images, &text);
                prompts.push((message_id, text, prompt, sent));
            }
            let text = prompts
                .iter()
                .map(|(_, fallback, prompt, _)| prompt.prompt_text.as_ref().unwrap_or(fallback))
                .cloned()
                .collect::<Vec<_>>()
                .join("\n\n");
            let mut merged = PreparedPrompt::from_text(text.clone());
            merged.images = prompts
                .iter()
                .flat_map(|(_, _, prompt, _)| prompt.images.iter().cloned())
                .collect();
            let covered = prompts
                .into_iter()
                .filter(|(_, _, _, sent)| !sent)
                .map(|(message_id, text, prompt, _)| (message_id, text, prompt.images))
                .collect();
            Ok::<_, String>((text, merged, covered))
        }
        .await;
        let (covered, outcome) = match prepared {
            Ok((text, prepared, covered)) => {
                let params = TurnQueueReplaceParams {
                    idempotency_key: new_message_id(),
                    session_id,
                    queue_item_id,
                    entries: vec![entry(server_message_id.clone(), &prepared, &text)],
                };
                (covered, replace_request(&client, params).await)
            }
            Err(error) => {
                tracing::warn!(%error, "queued prompt preparation failed");
                (Vec::new(), ReplacementOutcome::Failed)
            }
        };
        let delivered = members
            .into_iter()
            .filter(|(_, ever_sent)| *ever_sent || matches!(outcome, ReplacementOutcome::Replaced))
            .map(|(message_id, _)| message_id)
            .collect();
        let event = QueueEvent::GroupReplaced {
            server_message_id,
            revision,
            covered,
            delivered,
            outcome,
        };
        crate::input::deliver(tx, event, &pending).await;
    });
}

async fn replace_request(
    client: &Arc<Client>,
    params: TurnQueueReplaceParams,
) -> ReplacementOutcome {
    let Ok(value) = serde_json::to_value(params) else {
        return ReplacementOutcome::Failed;
    };
    match client.request(method::TURN_QUEUE_REPLACE, value).await {
        Ok(_) => ReplacementOutcome::Replaced,
        Err(error)
            if error
                .downcast_ref::<RequestFailure>()
                .is_some_and(RequestFailure::is_not_found) =>
        {
            ReplacementOutcome::Consumed
        }
        Err(error) => {
            tracing::warn!(%error, method = method::TURN_QUEUE_REPLACE, "queue command failed");
            ReplacementOutcome::Failed
        }
    }
}
