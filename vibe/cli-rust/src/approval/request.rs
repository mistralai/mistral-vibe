//! Approval callback response requests.

use std::sync::Arc;

use super::Event;
use crate::app::App;
use crate::server::{method, ApprovalCallback, ApprovalDecisionType, CallbackOutput, Client};

pub(super) fn send(
    app: &App,
    client: &Arc<Client>,
    callback: &ApprovalCallback,
    decision: ApprovalDecisionType,
) -> bool {
    let output = CallbackOutput::approval(decision);
    let Some(params) = callback
        .address
        .result_params(app.session.session_id.as_deref(), &output)
    else {
        tracing::error!(
            callback_id = callback.address.callback_id,
            "approval has no session"
        );
        return false;
    };
    let callback_id = callback.address.callback_id.clone();
    let Some(tx) = app.events.clone() else {
        tracing::error!(callback_id, "approval response channel is unavailable");
        return false;
    };
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
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
