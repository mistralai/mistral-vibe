//! The panel's app-server calls, each answering with one `Event`.

use std::future::Future;
use std::sync::Arc;

use serde_json::json;

use super::actions::{Acted, Outcome};
use super::{Action, Event, UNSUPPORTED};
use crate::app::App;
use crate::server::request_error::describe;
use crate::server::{
    method, Client, RequestFailure, SessionArchiveParams, StorageLeftoversCleanParams,
    StorageRefreshParams, StorageRefreshResponse, StorageSessionsDeleteParams, StorageUsage,
};

/// `storage/usage/read`; an older server answers "method not found".
pub(super) fn read(app: &App, client: &Arc<Client>) {
    spawn(app, client, |client| async move {
        Event::Read(read_usage(&client).await.map(Box::new))
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

/// One archive or unarchive.
pub(super) fn act(app: &App, client: &Arc<Client>, action: Action, id: String) {
    spawn(app, client, move |client| async move {
        let outcome = archive(&client, &id, action == Action::Archive).await;
        Event::Acted(Acted {
            action,
            session_id: id,
            outcome,
        })
    });
}

/// Archive the selection one session at a time, each refused while it works.
pub(super) fn bulk(app: &App, client: &Arc<Client>, ids: Vec<String>) {
    spawn(app, client, move |client| async move {
        let mut acted = Vec::with_capacity(ids.len());
        for id in ids {
            let outcome = archive(&client, &id, true).await;
            acted.push(Acted {
                action: Action::Archive,
                session_id: id,
                outcome,
            });
        }
        Event::Bulk(acted)
    });
}

/// `storage/sessions/delete`: the server deletes each session and the worktrees no session still
/// claims, then refreshes itself.
pub(super) fn delete_sessions(app: &App, client: &Arc<Client>, session_ids: Vec<String>) {
    spawn(app, client, move |client| async move {
        let params = StorageSessionsDeleteParams { session_ids };
        let params = serde_json::to_value(params).unwrap_or_default();
        let result = client
            .request(method::STORAGE_SESSIONS_DELETE, params)
            .await;
        Event::Deleted(
            result
                .map(|v| crate::utils::json::read(&v))
                .map_err(|e| describe(&e)),
        )
    });
}

/// `storage/leftovers/clean`: the server removes what it still finds safe and refreshes itself.
pub(super) fn clean(app: &App, client: &Arc<Client>, params: StorageLeftoversCleanParams) {
    spawn(app, client, move |client| async move {
        let params = serde_json::to_value(params).unwrap_or_default();
        let result = client
            .request(method::STORAGE_LEFTOVERS_CLEAN, params)
            .await;
        Event::Cleaned(
            result
                .map(|v| crate::utils::json::read(&v))
                .map_err(|e| describe(&e)),
        )
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

/// `session/archive` refuses with `data.refusal: "busy"` or a session-busy conflict.
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
