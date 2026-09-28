//! Approval callback response requests.

use std::sync::Arc;

use serde_json::json;

use super::Event;
use crate::app::App;
use crate::server::{method, ApprovalCallback, ApprovalDecisionType, Client};

pub(super) fn send(
    app: &App,
    client: &Arc<Client>,
    callback: &ApprovalCallback,
    decision: ApprovalDecisionType,
) -> bool {
    let session_id = match callback.session_id.as_str() {
        "" => app.session.session_id.clone(),
        session_id => Some(session_id.to_owned()),
    };
    let Some(session_id) = session_id else {
        tracing::error!(
            callback_id = callback.callback_id,
            "approval has no session"
        );
        return false;
    };
    let callback_id = callback.callback_id.clone();
    let Some(tx) = app.approval.tx.clone() else {
        tracing::error!(callback_id, "approval response channel is unavailable");
        return false;
    };
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let params = json!({
            "sessionId": session_id,
            "result": {
                "callbackId": callback_id,
                "error": null,
                "output": {
                    "type": "approval",
                    "decision": {"type": decision},
                    "feedback": null,
                },
            },
        });
        let event = match client.request(method::CALLBACK_RESULT, params).await {
            Ok(_) => Event::Responded { callback_id },
            Err(error) => {
                tracing::warn!(%error, "failed to answer approval callback");
                Event::Failed {
                    callback_id,
                    error: error.to_string(),
                }
            }
        };
        crate::input::deliver(Some(tx), event, &pending).await;
    });
    true
}
