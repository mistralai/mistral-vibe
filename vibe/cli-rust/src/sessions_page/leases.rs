//! Which sessions another Vibe process holds open, watched once per process.

use std::collections::HashSet;
use std::sync::Arc;

use serde_json::{json, Value};

use super::Event;
use crate::app::App;
use crate::server::{method, Client};

/// Watch the session leases once per process, so the locks are warm before the page opens.
pub fn subscribe(app: &mut App, client: &Arc<Client>) {
    if app.sessions_page.subscribed || app.events.is_none() {
        return;
    }
    app.sessions_page.subscribed = true;
    let own_pid = client.server_pid();
    let current = app.session.session_id.clone();
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        match client
            .request(method::SESSION_LEASE_SUBSCRIBE, json!({}))
            .await
        {
            Ok(value) => Event::Leases(held(&value, own_pid, current.as_deref())),
            // A pre-lease server or replay fixture has no watch; resume stays authoritative.
            Err(error) => {
                tracing::debug!(%error, "session lease subscribe failed");
                Event::Leases(HashSet::new())
            }
        }
    });
}

/// One `session/lease/updated` notification: the lock set follows it.
pub fn on_updated(app: &mut App, client: &Client, params: &Value) {
    let Some(id) = params.get("sessionId").and_then(Value::as_str) else {
        return;
    };
    let is_current = app.session.session_id.as_deref() == Some(id);
    let id = id.to_owned();
    let locked = lease_locked(params.get("leaseHolder"), client.server_pid(), is_current);
    // Recorded before the subscribe answer, which can arrive later on its own channel.
    app.sessions_page.lease_notified.insert(id.clone());
    if locked {
        app.sessions_page.locked.insert(id);
    } else {
        app.sessions_page.locked.remove(&id);
    }
}

/// The lease watch died; stale locks are worse than none.
pub fn on_ended(app: &mut App) {
    app.sessions_page.locked.clear();
    app.sessions_page.lease_ended = true;
}

/// A subscribe snapshot under newer lease notifications; ignored after the watch ends.
pub fn apply_snapshot(app: &mut App, mut snapshot: HashSet<String>) {
    let page = &mut app.sessions_page;
    if page.lease_ended {
        return;
    }
    snapshot.retain(|id| !page.lease_notified.contains(id));
    snapshot.extend(
        page.lease_notified
            .iter()
            .filter(|id| page.locked.contains(*id))
            .cloned(),
    );
    page.locked = snapshot;
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
