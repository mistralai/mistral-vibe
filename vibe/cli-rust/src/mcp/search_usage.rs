//! Record search adoption once per browser opening without sending query text.

use std::sync::Arc;

use crate::app::App;
use crate::input::deliver;
use crate::server::{method, Client, TelemetryRecordParams};

pub fn record(app: &mut App, client: &Arc<Client>) {
    if app.mcp.search.recorded || app.mcp.search.query.trim().is_empty() {
        return;
    }
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.mcp.tx.clone()) else {
        return;
    };
    app.mcp.search.recorded = true;
    let params = TelemetryRecordParams {
        session_id: session_id.clone(),
        name: "vibe.mcp_search_used".to_owned(),
        properties: std::collections::BTreeMap::from([(
            "metadata".to_owned(),
            serde_json::json!({"session_id": session_id, "call_source": "vibe_code"}),
        )]),
        correlate_last_request: false,
    };
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        if let Ok(params) = serde_json::to_value(params) {
            let _ = client.request(method::TELEMETRY_RECORD, params).await;
        }
        deliver(Some(tx), super::Event::SearchRecorded, &pending).await;
    });
}
