//! Acknowledges server-initiated `callback/call` requests.

use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::{json, Value};
use tokio::sync::{mpsc, oneshot};

use crate::server::{method, notification, server_method, Notification, Request};

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
    let (Some(callback_id), Some(kind)) = (
        callback
            .and_then(|c| c.get("callbackId"))
            .and_then(Value::as_str),
        callback
            .and_then(|c| c.pointer("/detail/kind"))
            .and_then(Value::as_str),
    ) else {
        tracing::warn!(?params, "callback/call missing callbackId or kind");
        return None;
    };
    let session_id = callback
        .and_then(|c| c.get("sessionId"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let (callback_id, kind) = (callback_id.to_owned(), kind.to_owned());

    // Acknowledge delivery (the response the server is blocking on).
    let ack = json!({
        "jsonrpc": "2.0",
        "id": id,
        "result": {"callbackId": &callback_id, "accepted": true},
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
        send_deny(wiring, &callback_id, &kind, &session_id).await;
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

/// Deny a callback with the correct output shape per kind, mirroring Python
/// `session.deny_callback`.
async fn send_deny(wiring: DenyWiring<'_>, callback_id: &str, kind: &str, session_id: &str) {
    let DenyWiring {
        pending,
        notif_tx,
        to_writer,
        next_request_id,
        active_session,
    } = wiring;
    let output = match kind {
        "approval" => json!({
            "type": "approval",
            "decision": {"type": "deny"},
            "feedback": null,
        }),
        "user_input" => json!({
            "type": "user_input",
            "result": {"answers": [], "cancelled": true},
        }),
        _ => return,
    };
    // The tolerated callback shape omits `sessionId` (ADR 0014); an empty id
    // would be rejected, so fall back to the active session like Python does.
    let session_id = if session_id.is_empty() {
        active_session
            .lock()
            .unwrap_or_else(|err| err.into_inner())
            .clone()
            .unwrap_or_default()
    } else {
        session_id.to_owned()
    };
    let result_id = next_request_id.fetch_add(1, Ordering::Relaxed);
    let params = json!({
        "sessionId": session_id,
        "result": {
            "callbackId": callback_id,
            "output": output,
        },
    });
    // Register the result so a server rejection is delivered, not silently
    // dropped: without an entry the turn would wait forever on `turn/completed`.
    let (tx, rx) = oneshot::channel();
    pending
        .lock()
        .unwrap_or_else(|err| err.into_inner())
        .insert(result_id, tx);
    if let Ok(frame) =
        serde_json::to_string(&Request::call(result_id, method::CALLBACK_RESULT, params))
    {
        if to_writer.send(Some(frame)).is_err() {
            pending
                .lock()
                .unwrap_or_else(|err| err.into_inner())
                .remove(&result_id);
            return;
        }
    }
    let callback_id = callback_id.to_owned();
    let notif_tx = notif_tx.clone();
    tokio::spawn(async move {
        match rx.await {
            Ok(Ok(_)) => {}
            Ok(Err(failure)) => {
                let message = failure.to_string();
                tracing::error!(callback_id, message, "callback/result denial rejected");
                let _ = notif_tx
                    .send(Notification {
                        method: notification::CALLBACK_RESULT_FAILED.into(),
                        params: Value::String(message),
                    })
                    .await;
            }
            // The reader drains pending entries on shutdown; nothing to report.
            Err(_) => {}
        }
    });
}
