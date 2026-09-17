//! `/compact`: send `session/compact` and show a status indicator until the
//! server answers with `session/compacted`.

use std::sync::Arc;

use serde_json::{json, Value};

use super::event::{dispatch, CommandEvent};
use super::submission::new_message_id;
use crate::app::{App, Status};
use crate::server::{method, Client};
use crate::transcript::local;

/// Start compaction. Does nothing while a turn is generating (Python
/// `_agent_job_active` guard) or if there is no conversation history.
pub fn start_compact(app: &mut App, client: &Arc<Client>, value: &str) {
    if matches!(app.session.status, Status::Generating { .. }) {
        local::add_command_error(
            &mut app.view.transcript,
            &new_message_id(),
            "Cannot compact while agent loop is processing. Please wait.",
        );
        return;
    }
    // The /compact echo above is local; only server rows count as history
    // (Python checks `app_server.history`, which has no command echo in it).
    if app.view.transcript.lines().all(|entry| entry.local) {
        local::add_command_error(
            &mut app.view.transcript,
            &new_message_id(),
            "No conversation history to compact yet.",
        );
        return;
    }
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let extra = value
        .split_once(char::is_whitespace)
        .map(|(_, rest)| rest.trim().to_owned())
        .filter(|s| !s.is_empty())
        .unwrap_or_default();
    let Some((_, tx)) = dispatch(app) else {
        return;
    };
    app.compacting = true;
    let status_id = new_message_id();
    local::add_compact_status(&mut app.view.transcript, &status_id);
    let client = client.clone();
    tokio::spawn(async move {
        let params = json!({
            "sessionId": session_id,
            "extraInstructions": extra,
        });
        let event = match client.request(method::SESSION_COMPACT, params).await {
            Ok(result) => match result.get("state").cloned().and_then(|state| {
                serde_json::from_value::<crate::server::PublicSessionState>(state).ok()
            }) {
                Some(state) => CommandEvent::Compacted {
                    state,
                    status_id: status_id.clone(),
                },
                None => CommandEvent::CompactError {
                    status_id: status_id.clone(),
                    error: "Compaction returned no state.".to_owned(),
                },
            },
            Err(error) => CommandEvent::CompactError {
                status_id: status_id.clone(),
                error: error.to_string(),
            },
        };
        let _ = tx.try_send(event);
    });
}

/// Settle the compaction state on the main thread (Python `session/compacted`).
pub fn settle_compact(app: &mut App) {
    app.compacting = false;
}

/// Settle and adopt the session a compaction handed off to (Python `replace_state`).
pub fn apply_compacted(app: &mut App, session_id: String) {
    settle_compact(app);
    app.session.session_id = Some(session_id);
}

/// Read the replacement session a `session/compacted` handoff carries.
pub fn compacted_session_id(params: &Value) -> Option<String> {
    params
        .get("state")
        .cloned()
        .and_then(|state| serde_json::from_value::<crate::server::PublicSessionState>(state).ok())
        .map(|state| state.session.id)
        .or_else(|| {
            params
                .get("sessionId")
                .and_then(Value::as_str)
                .filter(|id| !id.is_empty())
                .map(str::to_owned)
        })
}

/// Apply a manual `session/compact` response (Python `_run_compact` success path).
pub fn apply_manual_compacted(
    app: &mut App,
    state: crate::server::PublicSessionState,
    status_id: &str,
) {
    apply_compacted(app, state.session.id);
    // Python `clear_server_queue`: dropped prompts lose their rows too.
    let ids: Vec<String> = app
        .queue
        .items
        .iter()
        .map(|item| item.message_id.clone())
        .collect();
    for id in &ids {
        app.view.transcript.remove(id);
    }
    app.queue.clear();
    app.session.tokens = (0, app.session.tokens.1);
    local::settle_compact_status(&mut app.view.transcript, status_id, None);
}
