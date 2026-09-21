//! User-invocable skill telemetry.

use std::collections::BTreeMap;
use std::sync::Arc;

use serde_json::Value;

use crate::app::App;
use crate::server::{method, Client, TelemetryRecordParams};

pub fn record_usage(app: &App, client: &Arc<Client>, name: String) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let params = TelemetryRecordParams {
            session_id,
            name: "vibe.slash_command_used".to_owned(),
            properties: BTreeMap::from([
                ("command".to_owned(), Value::String(name)),
                ("command_type".to_owned(), Value::String("skill".to_owned())),
            ]),
            correlate_last_request: false,
        };
        if let Ok(value) = serde_json::to_value(params) {
            let _ = client.request(method::TELEMETRY_RECORD, value).await;
        }
    });
}
