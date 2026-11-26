//! The connection's first-party credential headers, for client-owned requests.

use std::collections::BTreeMap;
use std::time::Duration;

use serde::Deserialize;
use serde_json::json;

use crate::server::Client;

pub const API_KEY_MARKER: &str = "vibe-cli-oidc";

pub fn enabled() -> bool {
    std::env::var("VIBE_CLI_OIDC_ENABLED")
        .is_ok_and(|value| matches!(value.to_lowercase().as_str(), "1" | "true"))
}

#[derive(Deserialize)]
struct HeadersResponse {
    headers: BTreeMap<String, String>,
}

pub async fn headers(
    client: Option<&Client>,
    url: &str,
    api_key: &str,
) -> Result<BTreeMap<String, String>, String> {
    if api_key != API_KEY_MARKER {
        return Ok(BTreeMap::from([(
            "Authorization".into(),
            format!("Bearer {api_key}"),
        )]));
    }
    let client = client.ok_or("OIDC authentication needs the app server")?;
    let value = tokio::time::timeout(
        Duration::from_secs(60),
        client.request_err("platformCredentials/headers", json!({"url": url})),
    )
    .await
    .map_err(|_| "OIDC credential resolution timed out")?
    .map_err(failure_message)?;
    let response: HeadersResponse =
        serde_json::from_value(value).map_err(|_| "Invalid OIDC credential response")?;
    if !response.headers.contains_key("Authorization") {
        return Err("OIDC credential response has no authorization".into());
    }
    Ok(response.headers)
}

/// The server's own message when its credential was refused after a successful refresh.
fn failure_message(error: crate::server::RpcError) -> String {
    let reason = error.data.as_ref().and_then(|d| d.get("reason"));
    if reason.and_then(|r| r.as_str()) == Some("unauthorized_after_refresh") {
        return error.message;
    }
    "OIDC credential resolution failed; explicitly re-login".into()
}
