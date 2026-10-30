//! Fresh OIDC headers for first-party client-owned requests.

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
        client.request("setup/oidc-headers", json!({"url": url})),
    )
    .await
    .map_err(|_| "OIDC credential resolution timed out")?
    .map_err(|_| "OIDC credential resolution failed; explicitly re-login")?;
    let response: HeadersResponse =
        serde_json::from_value(value).map_err(|_| "Invalid OIDC credential response")?;
    if !response.headers.contains_key("Authorization") {
        return Err("OIDC credential response has no authorization".into());
    }
    Ok(response.headers)
}
