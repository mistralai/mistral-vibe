//! Background and manual re-discovery of the open `/mcp` browser's sources.

use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use super::{Event, BACKGROUND_REFRESH_INTERVAL};
use crate::app::App;
use crate::input::deliver;
use crate::server::{method, Client};

/// Re-discover sources for the open browser (Python `_start_refresh`), coalescing
/// with a refresh already in flight.
pub fn refresh(app: &mut App, client: &Arc<Client>) {
    if app.mcp.refreshing {
        return;
    }
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.mcp.tx.clone()) else {
        return;
    };
    app.mcp.refreshing = true;
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let params = json!({"sessionId": session_id});
        // Python's refresh worker drops failures, so they never reach the transcript.
        let response = match rediscover(&client, params).await {
            Ok(response) => Some(response),
            Err(error) => {
                tracing::warn!(%error, "MCP refresh failed");
                None
            }
        };
        deliver(Some(tx), Event::Refreshed(response), &pending).await;
    });
}

/// Python `_refresh_mcp_browser`: wait for init, then re-fetch connectors and servers.
pub(super) async fn rediscover(client: &Client, params: Value) -> anyhow::Result<Value> {
    client
        .request(method::SESSION_READY_WAIT, params.clone())
        .await?;
    // A connector outage must not keep local servers from being re-discovered.
    if let Err(error) = client
        .request(method::CONNECTOR_CATALOG_REFRESH, params.clone())
        .await
    {
        tracing::warn!(%error, "Connector catalog refresh failed");
    }
    client.request(method::MCP_REFRESH, params).await
}

/// When the background refresh fires; far away while none is scheduled.
pub fn refresh_deadline(app: &App) -> tokio::time::Instant {
    app.mcp
        .refresh_at
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(|| tokio::time::Instant::now() + Duration::from_secs(3600))
}

/// The background tick (Python `set_interval`): refresh and schedule the next one.
pub fn background_refresh(app: &mut App, client: &Arc<Client>) {
    app.mcp.refresh_at = Some(Instant::now() + BACKGROUND_REFRESH_INTERVAL);
    refresh(app, client);
}
