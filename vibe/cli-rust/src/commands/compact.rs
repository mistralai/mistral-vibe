//! `/compact`: send `session/compact` and show a status indicator until the
//! server answers with `session/compacted`.

use std::sync::Arc;

use serde_json::{json, Value};

use super::event::{dispatch, CommandEvent};
use super::submission::new_message_id;
use crate::app::{App, Status};
use crate::server::{method, Client, PublicSessionState};
use crate::transcript::local;

/// Start compaction. Does nothing while a turn is generating or if there is
/// no conversation history.
pub fn start_compact(app: &mut App, client: &Arc<Client>, value: &str) {
    if matches!(app.session.status, Status::Generating { .. }) {
        local::add_command_error(
            &mut app.view.transcript,
            &new_message_id(),
            "Cannot compact while agent loop is processing. Please wait.",
        );
        return;
    }
    // Only server rows count as history; local rows are client notices.
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
    crate::work::spawn(async move {
        let params = json!({
            "sessionId": session_id,
            "extraInstructions": extra,
        });
        let event = match client.request(method::SESSION_COMPACT, params).await {
            Ok(result) => match result
                .get("state")
                .cloned()
                .and_then(|state| serde_json::from_value::<PublicSessionState>(state).ok())
            {
                Some(state) => CommandEvent::Compacted {
                    state: Box::new(state),
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

/// Settle the compaction state on the main thread (on `session/compacted`).
pub fn settle_compact(app: &mut App) {
    app.compacting = false;
}

/// Settle and adopt the session a compaction handed off to.
pub fn apply_compacted(app: &mut App, state: &PublicSessionState) {
    settle_compact(app);
    crate::background_work::adopt_session(app, state);
    crate::subagents::refresh(app);
}

/// Apply a `session/compacted` handoff. The bare `sessionId` fallback has no
/// state, so the replacement starts a fresh lineage.
pub fn apply_compacted_notification(app: &mut App, params: &Value) {
    if let Some(state) = handoff_state(params) {
        return apply_compacted(app, &state);
    }
    settle_compact(app);
    let Some(id) = params["sessionId"].as_str().filter(|id| !id.is_empty()) else {
        return;
    };
    app.set_session_id(id.to_owned());
    app.subagents.seed_snapshot(Vec::new());
    crate::subagents::refresh(app);
}

/// The replacement state a `session/compacted` or `session/contextCleared` handoff carries.
pub fn handoff_state(params: &Value) -> Option<PublicSessionState> {
    serde_json::from_value(params.get("state")?.clone()).ok()
}

/// Apply a successful manual `session/compact` response.
pub fn apply_manual_compacted(app: &mut App, state: PublicSessionState, status_id: &str) {
    apply_compacted(app, &state);
    // The server queue is cleared: dropped prompts lose their rows too.
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
    app.subagents.main_tokens = app.session.tokens;
    local::settle_compact_status(&mut app.view.transcript, status_id, None);
}
