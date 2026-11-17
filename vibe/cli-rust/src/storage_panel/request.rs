//! The panel's app-server calls, each answering with one `Event`.

use std::future::Future;
use std::sync::Arc;

use serde_json::json;

use super::actions::{Acted, Outcome, Worktree};
use super::{Action, Event, UNSUPPORTED};
use crate::app::App;
use crate::server::request_error::describe;
use crate::server::{
    method, Client, RequestFailure, SessionArchiveParams, StorageRefreshParams,
    StorageRefreshResponse, StorageUsage,
};

/// `storage/usage/read`; an older server answers "method not found".
pub(super) fn read(app: &App, client: &Arc<Client>) {
    spawn(app, client, |client| async move {
        Event::Read(read_usage(&client).await)
    });
}

async fn read_usage(client: &Client) -> Result<StorageUsage, String> {
    match client.request(method::STORAGE_USAGE_READ, json!({})).await {
        Ok(value) => Ok(crate::utils::json::read(&value)),
        Err(error) if method_not_found(&error) => Err(UNSUPPORTED.to_owned()),
        Err(error) => Err(describe(&error)),
    }
}

/// `storage/usage/refresh`; its result arrives as `storage/usage/updated`.
pub(super) fn refresh(app: &App, client: &Arc<Client>, params: StorageRefreshParams, bulk: bool) {
    spawn(app, client, move |client| async move {
        let params = serde_json::to_value(params).unwrap_or_default();
        let result = client.request(method::STORAGE_USAGE_REFRESH, params).await;
        if let Err(error) = &result {
            tracing::debug!(%error, "storage refresh failed");
        }
        let response = result
            .as_ref()
            .map(crate::utils::json::read::<StorageRefreshResponse>);
        let scanning = response
            .as_ref()
            .is_ok_and(|r| r.started || r.scan_id.is_some());
        let answer = response.map(|r| r.scan_id).map_err(|_| ());
        Event::Refreshed {
            bulk,
            answer,
            scanning,
        }
    });
}

/// One archive, unarchive or delete; a delete then removes the worktree no saved session shares.
pub(super) fn act(
    app: &App,
    client: &Arc<Client>,
    action: Action,
    id: String,
    worktree: Option<String>,
) {
    spawn(app, client, move |client| async move {
        let outcome = match action {
            Action::Archive => archive(&client, &id, true).await,
            Action::Unarchive => archive(&client, &id, false).await,
            Action::Delete => outcome(crate::resume_picker::delete_session(&client, &id).await),
        };
        let worktree = match (&outcome, worktree) {
            (Outcome::Done, Some(path)) => removal(&client, path).await,
            _ => Worktree::None,
        };
        Event::Acted(Acted {
            action,
            session_id: id,
            outcome,
            worktree,
        })
    });
}

/// Archive the selection one session at a time, each refused while it works.
pub(super) fn bulk(app: &App, client: &Arc<Client>, ids: Vec<String>) {
    spawn(app, client, move |client| async move {
        let mut outcomes = Vec::with_capacity(ids.len());
        for id in ids {
            let outcome = archive(&client, &id, true).await;
            outcomes.push((id, outcome));
        }
        Event::Bulk(outcomes)
    });
}

async fn archive(client: &Client, id: &str, archived: bool) -> Outcome {
    let params = SessionArchiveParams {
        session_id: id.to_owned(),
        archived,
        require_idle: archived,
    };
    let params = serde_json::to_value(params).unwrap_or_default();
    outcome(client.request(method::SESSION_ARCHIVE, params).await)
}

/// Removed only when no saved session is left in it now, as the runner decides:
/// the snapshot can miss a session saved since its scan or list one deleted since.
async fn removal(client: &Arc<Client>, path: String) -> Worktree {
    match saved_sessions_in(client, &path).await {
        Some(0) => Worktree::Removal(
            crate::worktree_exit::remove_request_inner(client, &path, false, None, false, false)
                .await,
        ),
        Some(left) => Worktree::Shared(left),
        None => Worktree::Removal(None),
    }
}

/// Saved sessions whose folder is the worktree or below it, archived ones
/// included; `None` when the list fails. `session/list` matches a `cwd`
/// exactly, so every page is read and filtered here.
async fn saved_sessions_in(client: &Client, path: &str) -> Option<usize> {
    let root = std::path::Path::new(path);
    let mut count = 0;
    let mut cursor: Option<String> = None;
    loop {
        let mut params = json!({"includeArchived": true, "limit": 500});
        if let Some(cursor) = &cursor {
            params["cursor"] = json!(cursor);
        }
        let value = client
            .request(method::SESSION_LIST, params)
            .await
            .inspect_err(|error| tracing::debug!(%error, "listing saved sessions failed"))
            .ok()?;
        let items = value["items"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default();
        count += items
            .iter()
            .filter_map(|session| session["cwd"].as_str())
            .filter(|cwd| std::path::Path::new(cwd).starts_with(root))
            .count();
        match value["nextCursor"].as_str() {
            Some(next) => cursor = Some(next.to_owned()),
            None => return Some(count),
        }
    }
}

fn outcome(result: anyhow::Result<serde_json::Value>) -> Outcome {
    let Err(error) = result else {
        return Outcome::Done;
    };
    let busy = matches!(
        error.downcast_ref::<RequestFailure>(),
        Some(RequestFailure::Rpc(rpc)) if refused_busy(rpc)
    );
    match busy {
        true => Outcome::Busy,
        false => Outcome::Failed(describe(&error)),
    }
}

/// `session/archive` refuses with `data.refusal: "busy"`; `session/delete` with a session-busy conflict.
fn refused_busy(rpc: &crate::server::RpcError) -> bool {
    let refusal = rpc
        .data
        .as_ref()
        .and_then(|d| d.get("refusal"))
        .and_then(|r| r.as_str());
    rpc.code() == "conflict" && refusal == Some("busy") || rpc.is_session_busy()
}

fn method_not_found(error: &anyhow::Error) -> bool {
    matches!(
        error.downcast_ref::<RequestFailure>(),
        Some(RequestFailure::Rpc(rpc)) if rpc.is_method_not_found()
    )
}

/// Spawn one call; skipped once the answer channel is gone.
fn spawn<F>(app: &App, client: &Arc<Client>, request: impl FnOnce(Arc<Client>) -> F)
where
    F: Future<Output = Event> + Send + 'static,
{
    if app.events.is_some() {
        crate::ui_event::spawn(app, request(client.clone()));
    }
}
