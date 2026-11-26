//! Prompt preparation and typed queue-entry construction.

use std::sync::Arc;

use serde_json::json;

use crate::collapsed_pastes::{CollapsedPastes, DISPLAY_ANNOTATION};
use crate::server::{
    method, Client, ImageAttachment, PreparedPrompt, RequestFailure, TurnInputEntry,
};
use crate::{image_placeholders, paste_path};

/// Expand mentions and snapshot attachments before a prompt enters the queue.
/// `[Image #N]` placeholders of `images` are prepared as the image mentions
/// they stand for, then restored in the text the model reads.
pub(super) async fn prepare(
    client: &Arc<Client>,
    session_id: &str,
    text: &str,
    images: &[ImageAttachment],
) -> Result<PreparedPrompt, String> {
    let expanded = image_placeholders::expand(text, images);
    let mut prepared = prepare_text(client, session_id, &expanded).await?;
    if expanded != text {
        image_placeholders::collapse(&mut prepared, text, images)?;
    }
    Ok(prepared)
}

async fn prepare_text(
    client: &Arc<Client>,
    session_id: &str,
    text: &str,
) -> Result<PreparedPrompt, String> {
    let params = json!({
        "sessionId": session_id,
        "message": text,
        "titleContent": null,
    });
    match client
        .request(method::WORKSPACE_PROMPT_PREPARE, params)
        .await
    {
        Ok(response) => Ok(PreparedPrompt::from_response(&response, text)),
        Err(error) => {
            let rejected = error
                .downcast_ref::<RequestFailure>()
                .is_some_and(RequestFailure::is_invalid_params);
            // Raw text carries no image snapshot, so an image would be lost or sent unreadable.
            if rejected || !paste_path::image_mentions_in(text).is_empty() {
                return Err(format!("{error:#}"));
            }
            tracing::warn!(%error, "prompt preparation unavailable; using raw text");
            Ok(PreparedPrompt::from_text(text.to_owned()))
        }
    }
}

/// A queued user entry whose display content marks the collapsed pastes it sends.
pub(super) fn entry(
    entry_id: String,
    prompt: &PreparedPrompt,
    fallback: &str,
    pastes: &CollapsedPastes,
) -> TurnInputEntry {
    let text = prompt.prompt_text.as_deref().unwrap_or(fallback);
    TurnInputEntry {
        annotations: pastes
            .display(text)
            .map(|display| (DISPLAY_ANNOTATION.to_owned(), display))
            .into_iter()
            .collect(),
        content: prompt.content_blocks(fallback),
        entry_id,
        role: "user",
    }
}

pub(super) async fn request(
    client: &Arc<Client>,
    method: &str,
    params: impl serde::Serialize,
) -> Result<(), String> {
    let value = serde_json::to_value(params).map_err(|error| error.to_string())?;
    client.request(method, value).await.map_err(|error| {
        tracing::warn!(%error, method, "queue command failed");
        format!("{error:#}")
    })?;
    Ok(())
}
