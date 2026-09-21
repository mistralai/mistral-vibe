//! Prompt preparation and typed queue-entry construction.

use std::sync::Arc;

use serde_json::json;

use crate::server::{method, Client, PreparedPrompt, RequestFailure, TurnInputEntry};

/// Expand mentions and snapshot attachments before a prompt enters the queue.
pub(super) async fn prepare(
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
            if error
                .downcast_ref::<RequestFailure>()
                .is_some_and(RequestFailure::is_invalid_params)
            {
                return Err(error.to_string());
            }
            tracing::warn!(%error, "prompt preparation unavailable; using raw text");
            Ok(PreparedPrompt::from_text(text.to_owned()))
        }
    }
}

pub(super) fn entry(entry_id: String, prompt: &PreparedPrompt, fallback: &str) -> TurnInputEntry {
    TurnInputEntry {
        annotations: Default::default(),
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
        error.to_string()
    })?;
    Ok(())
}
