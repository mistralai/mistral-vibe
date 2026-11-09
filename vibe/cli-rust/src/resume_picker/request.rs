//! The picker's app-server calls, each answering with one `Event`.

use std::collections::HashSet;
use std::sync::Arc;

use serde::Deserialize;
use serde_json::{json, Value};

use super::{Event, Session};
use crate::app::App;
use crate::server::{
    method,
    request_error::{describe, resume_failure_message},
    Client,
};

/// How much history a preview reads.
const PREVIEW_LIMIT: u64 = 200;

pub(super) fn list(app: &App, client: &Arc<Client>, cwd: Option<String>) {
    spawn(app, client, |client| async move {
        match client
            .request(method::SESSION_LIST, json!({"cwd": cwd}))
            .await
        {
            Ok(value) => Event::Loaded(sessions(&value)),
            Err(error) => Event::Error(format!("Failed to list sessions: {error}")),
        }
    });
}

pub(super) fn preview(app: &App, client: &Arc<Client>, id: String, request: u64) {
    spawn(app, client, move |client| async move {
        let params = json!({"sessionId": id, "history": {"limit": PREVIEW_LIMIT}, "turns": null});
        state(client.request(method::SESSION_READ, params).await)
            .map(|state| Event::Preview { request, state })
            .unwrap_or(Event::PreviewFailed { request })
    });
}

pub(super) fn resume(app: &App, client: &Arc<Client>, id: String, params: Value) {
    spawn(app, client, |client| async move {
        match client.request(method::SESSION_RESUME, params).await {
            Ok(value) => match parse_state(&value) {
                Some(state) => Event::Resumed { id, state },
                None => Event::Error("Failed to resume session: malformed response.".into()),
            },
            Err(error) => Event::Error(resume_failure_message(&error)),
        }
    });
}

pub(super) fn delete(app: &App, client: &Arc<Client>, id: String) {
    spawn(app, client, |client| async move {
        let error = client
            .request(method::SESSION_DELETE, json!({"sessionId": id}))
            .await
            .err()
            .map(|error| describe(&error));
        Event::Deleted { id, error }
    });
}

/// Subscribe to the session-lease watch, answering with the locked set.
pub(super) fn leases(app: &App, client: &Arc<Client>, current: Option<String>) {
    let own_pid = client.server_pid();
    spawn(app, client, move |client| async move {
        match client
            .request(method::SESSION_LEASE_SUBSCRIBE, json!({}))
            .await
        {
            Ok(value) => Event::Leases(held(&value, own_pid, current.as_deref())),
            // A pre-lease server or replay fixture has no watch; resume stays authoritative.
            Err(error) => {
                tracing::debug!(%error, "session lease subscribe failed");
                Event::Leases(Default::default())
            }
        }
    });
}

/// The sessions a `session/lease/subscribe` snapshot holds elsewhere.
pub fn held(value: &Value, own_pid: Option<u32>, current: Option<&str>) -> HashSet<String> {
    value
        .get("held")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            let id = item.get("sessionId")?.as_str()?;
            lease_locked(item.get("leaseHolder"), own_pid, current == Some(id))
                .then(|| id.to_owned())
        })
        .collect()
}

/// Whether a lease held by another process locks the row. This client's own
/// app-server holds leases of its own — the attached session, its subagents,
/// maintenance sweeps — that are not locks; its pid comes from `initialize`.
/// A missing or torn holder pid is not a lock either.
pub fn lease_locked(holder: Option<&Value>, own_pid: Option<u32>, is_current: bool) -> bool {
    holder
        .and_then(|holder| holder.get("processId"))
        .and_then(Value::as_u64)
        .and_then(|pid| u32::try_from(pid).ok())
        .is_some_and(|pid| Some(pid) != own_pid && !is_current)
}

/// Spawn one call; skipped once the answer channel is gone.
fn spawn<F>(app: &App, client: &Arc<Client>, request: impl FnOnce(Arc<Client>) -> F)
where
    F: std::future::Future<Output = Event> + Send + 'static,
{
    if app.events.is_some() {
        crate::ui_event::spawn(app, request(client.clone()));
    }
}

fn state<E: std::fmt::Display>(
    result: Result<Value, E>,
) -> Option<crate::server::PublicSessionState> {
    result
        .inspect_err(|err| tracing::warn!(%err, "session preview failed"))
        .ok()
        .and_then(|value| parse_state(&value))
}

/// The `state` payload of a session lifecycle answer; a present-but-broken
/// state is logged, since only a contract break produces it.
fn parse_state(value: &Value) -> Option<crate::server::PublicSessionState> {
    match crate::server::PublicSessionState::deserialize(value.get("state")?) {
        Ok(parsed) => Some(parsed),
        Err(error) => {
            tracing::warn!(%error, "session state failed to parse");
            None
        }
    }
}

/// The saved sessions of a `session/list` answer, newest first.
pub(super) fn sessions(value: &Value) -> Vec<Session> {
    let mut items: Vec<Session> = crate::utils::json::lenient(&value["items"]).unwrap_or_default();
    items.sort_by_key(|session| std::cmp::Reverse(session.updated_at));
    items
}
