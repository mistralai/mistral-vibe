//! Teleport RPCs, each answered once through the bounded command-result channel.

use std::future::Future;
use std::sync::Arc;

use serde_json::{json, Value};

use super::Operation;
use crate::app::App;
use crate::commands::CommandEvent;
use crate::input::deliver;
use crate::server::proto_projects::{PickerView, RecoverResponse};
use crate::server::proto_teleport::TeleportError;
use crate::server::{Client, RequestFailure};

/// A failed teleport, from a `failed` event or a rejected request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Failure {
    pub code: Option<String>,
    pub message: String,
}

impl From<TeleportError> for Failure {
    fn from(error: TeleportError) -> Self {
        Self {
            code: error.code,
            message: error.message,
        }
    }
}

impl From<anyhow::Error> for Failure {
    fn from(error: anyhow::Error) -> Self {
        match error.downcast_ref::<RequestFailure>() {
            Some(RequestFailure::Rpc(rpc)) => Self {
                code: rpc.code.as_str().map(str::to_owned),
                message: rpc.message.clone(),
            },
            _ => Self {
                code: None,
                message: error.to_string(),
            },
        }
    }
}

pub enum Reply {
    /// `start` or `push/respond` answered; a failure ends the operation.
    Answered {
        operation_id: String,
        failure: Option<Failure>,
    },
    Cancelled {
        operation_id: String,
        cancelled: bool,
    },
    /// `vibeCode/projects/recover` answered; `view` is set once recovered.
    Recovered {
        session_id: String,
        picker_id: String,
        prompt: Option<String>,
        message: String,
        view: Option<Box<PickerView>>,
    },
}

pub(super) fn start(app: &App, client: &Arc<Client>, project_id: String) {
    let Some(op) = &app.teleport else { return };
    let params = operation_params(
        op,
        json!({"pickerId": op.picker_id, "prompt": op.prompt, "projectId": project_id}),
    );
    answered(app, client, "vibeCode/teleport/start", params);
}

pub(super) fn respond_to_push(app: &App, client: &Arc<Client>, approved: bool) {
    let Some(op) = &app.teleport else { return };
    let params = operation_params(op, json!({"approved": approved}));
    answered(app, client, "vibeCode/teleport/push/respond", params);
}

pub(super) fn cancel(app: &App, client: &Arc<Client>) {
    let Some(op) = &app.teleport else { return };
    let params = operation_params(op, json!({}));
    let operation_id = op.operation_id.clone();
    let client = client.clone();
    spawn(app, async move {
        let cancelled = client
            .request("vibeCode/teleport/cancel", params)
            .await
            .ok()
            .and_then(|value| value.get("cancelled").and_then(Value::as_bool))
            .unwrap_or(false);
        Reply::Cancelled {
            operation_id,
            cancelled,
        }
    });
}

pub(super) fn recover(app: &App, client: &Arc<Client>, op: Operation, message: String) {
    let params = json!({"sessionId": op.session_id, "pickerId": op.picker_id});
    let client = client.clone();
    spawn(app, async move {
        let view = client
            .request("vibeCode/projects/recover", params)
            .await
            .ok()
            .and_then(|value| serde_json::from_value::<RecoverResponse>(value).ok())
            .filter(|response| response.recovered)
            .map(|response| Box::new(response.view));
        Reply::Recovered {
            session_id: op.session_id,
            picker_id: op.picker_id,
            prompt: op.prompt,
            message,
            view,
        }
    });
}

/// `sessionId` and `operationId`, followed by the method's own `extra` fields.
fn operation_params(op: &Operation, extra: Value) -> Value {
    let mut params = json!({"sessionId": op.session_id, "operationId": op.operation_id});
    if let (Value::Object(params), Value::Object(extra)) = (&mut params, extra) {
        params.extend(extra);
    }
    params
}

fn answered(app: &App, client: &Arc<Client>, method: &'static str, params: Value) {
    let Some(op) = &app.teleport else { return };
    let operation_id = op.operation_id.clone();
    let client = client.clone();
    spawn(app, async move {
        let failure = client
            .request(method, params)
            .await
            .err()
            .map(Failure::from);
        Reply::Answered {
            operation_id,
            failure,
        }
    });
}

fn spawn(app: &App, reply: impl Future<Output = Reply> + Send + 'static) {
    let Some(tx) = app.command_tx.clone() else {
        return;
    };
    let pending = app.commit_started();
    tokio::spawn(async move {
        let reply = reply.await;
        deliver(Some(tx), CommandEvent::Teleport(Box::new(reply)), &pending).await;
    });
}
