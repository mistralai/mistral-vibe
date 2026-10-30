//! Background and manual re-discovery of the open `/mcp` browser's sources.

use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use super::{Event, BACKGROUND_REFRESH_INTERVAL};
use crate::app::App;
use crate::server::{method, Client};

/// Re-discover sources for the open browser, coalescing
/// with a refresh already in flight.
pub fn refresh(app: &mut App, client: &Arc<Client>) {
    if app.mcp.refreshing {
        return;
    }
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    app.mcp.refreshing = true;
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let params = json!({"sessionId": session_id});
        // Refresh failures are dropped, so they never reach the transcript.
        let response = rediscover(&client, params)
            .await
            .inspect_err(|error| tracing::warn!(%error, "MCP refresh failed"))
            .ok();
        Event::Refreshed(response)
    });
}

/// Wait for init, then re-fetch connectors and servers.
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

/// The background tick: refresh and schedule the next one.
pub fn background_refresh(app: &mut App, client: &Arc<Client>) {
    app.mcp.refresh_at = Some(Instant::now() + BACKGROUND_REFRESH_INTERVAL);
    refresh(app, client);
}
