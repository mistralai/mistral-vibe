//! `/branch`: fork the conversation into a detached, resumable session.

use std::sync::Arc;

use super::event::{dispatch, CommandEvent};
use crate::app::App;
use crate::server::{method, Client, SessionForkParams, SessionForkResponse};
use crate::transcript::local;

pub(super) fn branch_session(app: &mut App, client: &Arc<Client>) {
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let params = SessionForkParams {
            source_session_id: session_id.clone(),
            entry_id: None,
            attach: false,
        };
        let params = serde_json::to_value(params).unwrap_or_default();
        let event = match client.request_err(method::SESSION_FORK, params).await {
            Ok(value) => match serde_json::from_value::<SessionForkResponse>(value) {
                Ok(response) => CommandEvent::Branched {
                    old_session_id: session_id,
                    new_session_id: response.state.session.id,
                    harness: response.state.session.harness,
                },
                Err(error) => branch_error(&error.to_string()),
            },
            Err(error) => branch_error(&error.message),
        };
        let _ = tx.try_send(event);
    });
}

fn branch_error(message: &str) -> CommandEvent {
    CommandEvent::Error(format!("Failed to branch session: {message}"))
}

/// Mount the branch notice; unified records the fork server-side, other harnesses record it here.
pub fn apply_branched(
    app: &mut App,
    id: &str,
    old_session_id: &str,
    new_session_id: &str,
    harness: Option<&str>,
) {
    if harness != Some("unified") {
        crate::telemetry::session_branched(app, old_session_id, new_session_id);
    }
    local::add_branch_created(&mut app.view.transcript, id, old_session_id, new_session_id);
}
