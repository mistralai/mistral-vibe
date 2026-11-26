//! The two rewind round-trips, answered on the main thread as a `rewind::Event`.

use std::sync::Arc;

use serde::Deserialize;
use serde_json::Value;

use super::Event;
use crate::app::App;
use crate::server::{method, Client, PublicSessionState};

/// Ask whether rewinding to `messages[index]` would restore files; the panel
/// opens on the answer.
pub(super) fn select(
    app: &mut App,
    client: &Arc<Client>,
    messages: &[(usize, String, crate::collapsed_pastes::Collapsed)],
    index: usize,
) {
    let Some((entry_index, entry_id, preview)) = messages.get(index).cloned() else {
        return;
    };
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let params = serde_json::json!({"sessionId": session_id, "entryId": entry_id});
        match client
            .request_err(method::SESSION_REWIND_READ, params)
            .await
        {
            Ok(result) => Event::Selected {
                entry_index,
                entry_id,
                preview,
                has_file_changes: result["hasFileChanges"] == true,
            },
            Err(error) => Event::Failed(error.message),
        }
    });
}

/// Send the rewind the panel chose.
pub(super) fn confirm(app: &mut App, client: &Arc<Client>, inplace: bool) {
    let Some(entry_id) = app.rewind.entry_id.clone() else {
        return;
    };
    let restore_files = app.rewind.restore_files;
    rewind(app, client, entry_id, restore_files, inplace);
}

/// Send a rewind to the user message `entry_id`.
pub(super) fn rewind(
    app: &mut App,
    client: &Arc<Client>,
    entry_id: String,
    restore_files: bool,
    inplace: bool,
) -> bool {
    let Some(session_id) = app.session.session_id.clone() else {
        return false;
    };
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let params = serde_json::json!({
            "sessionId": session_id.clone(),
            "entryId": entry_id.clone(),
            "restoreFiles": restore_files,
            "inplace": inplace,
        });
        match client.request_err(method::SESSION_REWIND, params).await {
            Ok(result) => done(result, entry_id, session_id, inplace),
            Err(error) => Event::Failed(error.message),
        }
    });
    true
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RewindResponse {
    #[serde(default)]
    message: String,
    #[serde(default, deserialize_with = "crate::utils::json::lenient")]
    restore_errors: Vec<String>,
    state: Option<Box<PublicSessionState>>,
}

/// Read a `session/rewind` response into the event that applies it.
fn done(result: Value, entry_id: String, old_session_id: String, inplace: bool) -> Event {
    let Ok(RewindResponse {
        message,
        restore_errors,
        state: Some(state),
    }) = serde_json::from_value(result)
    else {
        return Event::Failed("Rewind returned no session state.".into());
    };
    Event::Done {
        entry_id,
        message,
        restore_errors,
        old_session_id,
        inplace,
        state,
    }
}

#[cfg(test)]
mod tests;
