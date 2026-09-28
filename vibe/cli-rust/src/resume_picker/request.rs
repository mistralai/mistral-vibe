//! The picker's app-server calls, each answering with one `Event`.

use std::sync::atomic::AtomicU32;
use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::mpsc::Sender;

use super::{Event, Session};
use crate::input::deliver;
use crate::server::{method, Client};

/// How much history a preview reads; Python's `create_resume_plan` default.
const PREVIEW_LIMIT: u64 = 200;

pub(super) struct Call {
    pub client: Arc<Client>,
    pub tx: Sender<Event>,
    pub pending: Arc<AtomicU32>,
}

pub(super) fn list(call: Call, cwd: Option<String>) {
    spawn(call, |client| async move {
        match client
            .request(method::SESSION_LIST, json!({"cwd": cwd}))
            .await
        {
            Ok(value) => Event::Loaded(sessions(&value)),
            Err(error) => Event::Error(format!("Failed to list sessions: {error}")),
        }
    });
}

pub(super) fn preview(client: Arc<Client>, tx: Sender<Event>, id: String, request: u64) {
    tokio::spawn(async move {
        // Python debounces highlights without counting the timer as off-thread
        // work, so a quick key sequence can settle before preview I/O starts.
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        let params = json!({"sessionId": id, "history": {"limit": PREVIEW_LIMIT}, "turns": null});
        let event = state(client.request(method::SESSION_READ, params).await)
            .map(|state| Event::Preview { request, state })
            .unwrap_or_else(|| Event::Error("Failed to load session preview.".into()));
        let _ = tx.send(event).await;
    });
}

pub(super) fn resume(call: Call, id: String, params: Value) {
    spawn(call, |client| async move {
        state(client.request(method::SESSION_RESUME, params).await)
            .map(|state| Event::Resumed { id, state })
            .unwrap_or_else(|| Event::Error("Failed to resume session.".into()))
    });
}

pub(super) fn delete(call: Call, id: String) {
    spawn(call, |client| async move {
        let error = client
            .request(method::SESSION_DELETE, json!({"sessionId": id}))
            .await
            .err()
            .map(|error| error.to_string());
        Event::Deleted { id, error }
    });
}

fn spawn<F>(call: Call, request: impl FnOnce(Arc<Client>) -> F + Send + 'static)
where
    F: std::future::Future<Output = Event> + Send,
{
    let Call {
        client,
        tx,
        pending,
    } = call;
    tokio::spawn(async move {
        let event = request(client).await;
        deliver(Some(tx), event, &pending).await;
    });
}

fn state<E: std::fmt::Display>(
    result: Result<Value, E>,
) -> Option<crate::server::PublicSessionState> {
    result
        .inspect_err(|err| tracing::warn!(%err, "session read or resume failed"))
        .ok()
        .and_then(|value| value.get("state").cloned())
        .and_then(|state| serde_json::from_value(state).ok())
}

/// The saved sessions of a `session/list` answer, newest first.
pub(super) fn sessions(value: &Value) -> Vec<Session> {
    let mut items: Vec<_> = value
        .get("items")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            Some(Session {
                id: item.get("id")?.as_str()?.to_owned(),
                title: text(item, "title"),
                preview: text(item, "preview"),
                updated_at: item
                    .get("updatedAt")
                    .and_then(Value::as_u64)
                    .unwrap_or_default(),
                cwd: item.get("cwd").and_then(Value::as_str).map(str::to_owned),
            })
        })
        .collect();
    items.sort_by_key(|session| std::cmp::Reverse(session.updated_at));
    items
}

fn text(item: &Value, key: &str) -> String {
    item.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}
