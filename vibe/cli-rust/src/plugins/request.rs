//! Plugin catalogue round-trips, answered on the main thread as `CommandEvent::Plugins`.

use std::future::Future;
use std::sync::Arc;

use serde_json::json;

use super::{text, Event};
use crate::app::App;
use crate::commands::CommandEvent;
use crate::input::deliver;
use crate::server::{method, Client, PluginCatalogState, RequestFailure};

/// A reload's changes and the catalogue after it; `None` from a backend without plugins.
pub type Diff = Option<(Vec<text::Change>, PluginCatalogState)>;

/// Run `task` off the main thread; false when there is no session to ask.
pub(super) fn spawn<F, Fut>(app: &mut App, client: &Arc<Client>, task: F) -> bool
where
    F: FnOnce(Arc<Client>, String) -> Fut + Send + 'static,
    Fut: Future<Output = Event> + Send + 'static,
{
    let Some(session_id) = app.session.session_id.clone() else {
        return false;
    };
    let tx = app.command_tx.clone();
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let event = task(client, session_id).await;
        deliver(tx, CommandEvent::Plugins(Box::new(event)), &pending).await;
    });
    true
}

/// Two reads either side of `plugin/reload` (Python `PluginCatalogResource.reload`).
pub(super) async fn reload_diff(client: &Client, session_id: &str) -> Result<Diff, String> {
    let Some(before) = read(client, session_id).await? else {
        return Ok(None);
    };
    client
        .request(method::PLUGIN_RELOAD, json!({"sessionId": session_id}))
        .await
        .map_err(|error| describe(&error))?;
    let Some(after) = read(client, session_id).await? else {
        return Ok(None);
    };
    Ok(Some((text::changes(&before, &after), after)))
}

pub(super) async fn read(
    client: &Client,
    session_id: &str,
) -> Result<Option<PluginCatalogState>, String> {
    let params = json!({"sessionId": session_id});
    let value = match client.request(method::PLUGIN_CATALOG_READ, params).await {
        Ok(value) => value,
        Err(error) if no_plugin_backend(&error) => return Ok(None),
        Err(error) => return Err(describe(&error)),
    };
    let catalog = value.get("plugins").cloned().unwrap_or_else(|| json!({}));
    serde_json::from_value(catalog)
        .map(Some)
        .map_err(|error| error.to_string())
}

/// The legacy backend answers plugin methods with `not_implemented` (Python `_NO_PLUGIN_BACKEND`).
fn no_plugin_backend(error: &anyhow::Error) -> bool {
    matches!(
        error.downcast_ref::<RequestFailure>(),
        Some(RequestFailure::Rpc(rpc))
            if matches!(rpc.code().as_str(), "not_implemented" | "method_not_found" | "-32601")
    )
}

fn describe(error: &anyhow::Error) -> String {
    match error.downcast_ref::<RequestFailure>() {
        Some(RequestFailure::Rpc(rpc)) if !rpc.message.is_empty() => rpc.message.clone(),
        _ => format!("{error:#}"),
    }
}
