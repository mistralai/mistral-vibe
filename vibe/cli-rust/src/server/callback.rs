//! Acknowledges server-initiated `callback/call` requests.

use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::server::{
    method, notification, server_method, CallbackAddress, CallbackOutput, Notification, Request,
};

use super::process::{ActiveSession, Pending};

/// The transport wiring the deny path needs, cloned from the reader loop.
pub struct DenyWiring<'a> {
    pub pending: &'a Pending,
    pub notif_tx: &'a mpsc::Sender<Notification>,
    pub to_writer: &'a mpsc::UnboundedSender<Option<String>>,
    pub next_request_id: &'a AtomicU64,
    pub active_session: &'a ActiveSession,
}

/// Acknowledge supported callbacks, returning the notification the reducer
/// should receive (None when the request is unsupported, malformed, or denied).
/// When `deny` is true (headless mode), both `approval` and `user_input` kinds
/// are answered with their deny output shapes and no notification is emitted.
pub async fn handle_server_request(
    id: u64,
    method_name: &str,
    params: Option<Value>,
    wiring: DenyWiring<'_>,
    deny: bool,
) -> Option<Notification> {
    let DenyWiring {
        to_writer,
        pending,
        notif_tx,
        next_request_id,
        active_session,
    } = wiring;
    if method_name != server_method::CALLBACK_CALL {
        tracing::debug!(method = method_name, "ignoring unsupported server request");
        return None;
    }
    let callback = params.as_ref().and_then(|p| p.get("callback"));
    let (Some(address), Some(kind)) = (
        callback.and_then(CallbackAddress::parse),
        callback
            .and_then(|c| c.pointer("/detail/kind"))
            .and_then(Value::as_str),
    ) else {
        tracing::warn!(?params, "callback/call missing callbackId or kind");
        return None;
    };
    let kind = kind.to_owned();

    // Acknowledge delivery (the response the server is blocking on).
    let ack = json!({
        "jsonrpc": "2.0",
        "id": id,
        "result": {"callbackId": &address.callback_id, "accepted": true},
    });
    if let Ok(frame) = serde_json::to_string(&ack) {
        let _ = to_writer.send(Some(frame));
    }

    if deny {
        let wiring = DenyWiring {
            pending,
            notif_tx,
            to_writer,
            next_request_id,
            active_session,
        };
        send_deny(wiring, &address, &kind).await;
        return None;
    }

    match kind.as_str() {
        "approval" | "user_input" => Some(Notification {
            method: server_method::CALLBACK_CALL.into(),
            params: params.unwrap_or(Value::Null),
        }),
        _ => {
            tracing::warn!(kind, "unadvertised callback kind, not answering");
            None
        }
    }
}

/// Deny a callback with the correct output shape per kind.
async fn send_deny(wiring: DenyWiring<'_>, address: &CallbackAddress, kind: &str) {
    let DenyWiring {
        pending,
        notif_tx,
        to_writer,
        next_request_id,
        active_session,
    } = wiring;
    let Some(output) = CallbackOutput::denial(kind) else {
        return;
    };
    let active = active_session
        .lock()
        .unwrap_or_else(|err| err.into_inner())
        .clone();
    let Some(params) = address.result_params(active.as_deref(), &output) else {
        tracing::error!(
            callback_id = address.callback_id,
            "callback denial has no session"
        );
        return report_failure(notif_tx, "callback has no session".into()).await;
    };
    let result_id = next_request_id.fetch_add(1, Ordering::Relaxed);
    // Register the result so a server rejection is delivered, not silently
    // dropped: without an entry the turn would wait forever on `turn/completed`.
    let rx = pending.register(result_id);
    if let Ok(frame) =
        serde_json::to_string(&Request::call(result_id, method::CALLBACK_RESULT, params))
    {
        if to_writer.send(Some(frame)).is_err() {
            pending.take(result_id);
            return;
        }
    }
    let callback_id = address.callback_id.clone();
    let notif_tx = notif_tx.clone();
    tokio::spawn(async move {
        match rx.await {
            Ok(Ok(_)) => {}
            Ok(Err(failure)) => {
                let message = failure.to_string();
                tracing::error!(callback_id, message, "callback/result denial rejected");
                report_failure(&notif_tx, message).await;
            }
            // The reader drains pending entries on shutdown; nothing to report.
            Err(_) => {}
        }
    });
}

/// Fail the headless turn instead of leaving it waiting on an unanswered callback.
async fn report_failure(notif_tx: &mpsc::Sender<Notification>, message: String) {
    let _ = notif_tx
        .send(Notification {
            method: notification::CALLBACK_RESULT_FAILED.into(),
            params: Value::String(message),
        })
        .await;
}
