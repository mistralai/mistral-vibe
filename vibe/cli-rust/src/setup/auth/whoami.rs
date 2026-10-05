//! Tenant-domain discovery (Python `vibe/setup/auth/whoami.py`): a custom
//! console advertises the tenant's own API and chat hosts on `/api/vibe/whoami`
//! and the wizard adopts them before writing provider URLs to config.

use std::time::Duration;

use serde::Deserialize;

use crate::setup::auth::rpc::ProviderView;

/// Fail fast enough not to stall the wizard: the call happens on the wizard
/// loop, which pauses redraws while it resolves.
const WHOAMI_TIMEOUT: Duration = Duration::from_secs(10);

/// `/api/vibe/whoami` — only the tenant fields matter here; extras are ignored.
#[derive(Deserialize)]
struct WhoAmI {
    api_base: Option<String>,
    vibe_base: Option<String>,
}

/// Fetch `/whoami` and return `(provider, vibe_base_url)` updated with any
/// tenant-advertised domains. Callers pass the current values; on any failure
/// or when both fields are missing, the inputs come back unchanged (Python
/// `resolve_tenant_domains`).
pub async fn resolve_tenant_domains(
    provider: &ProviderView,
    console_base_url: &str,
    api_key: &str,
    current_vibe_base_url: &str,
    enable_system_trust_store: bool,
) -> (ProviderView, String) {
    let mut provider = provider.clone();
    let mut vibe_base_url = current_vibe_base_url.to_owned();
    let Some(whoami) = fetch(console_base_url, api_key, enable_system_trust_store).await else {
        return (provider, vibe_base_url);
    };
    if let Some(api_base) = whoami.api_base.as_deref() {
        if let Some(sanitized) = sanitize_tenant_url(api_base) {
            provider.api_base = format!("{sanitized}/v1");
        }
    }
    if let Some(vibe_base) = whoami.vibe_base.as_deref() {
        if let Some(sanitized) = sanitize_tenant_url(vibe_base) {
            vibe_base_url = sanitized;
        }
    }
    (provider, vibe_base_url)
}

/// `GET {console_base_url}/api/vibe/whoami` with the bearer key; any failure
/// degrades to `None` (Python `fetch_whoami`).
async fn fetch(
    console_base_url: &str,
    api_key: &str,
    enable_system_trust_store: bool,
) -> Option<WhoAmI> {
    // The replay harness has no network: degrade to the failed-request path.
    if crate::utils::is_replaying() {
        return None;
    }
    let url = format!("{}/api/vibe/whoami", console_base_url.trim_end_matches('/'));
    let client = crate::update_notifier::gateway::wizard_http_client(
        WHOAMI_TIMEOUT,
        enable_system_trust_store,
    )
    .ok()?;
    let response = client
        .get(&url)
        .header("Authorization", format!("Bearer {api_key}"))
        .send()
        .await
        .ok()?;
    if !response.status().is_success() {
        return None;
    }
    response.json().await.ok()
}

/// Python `_sanitize_tenant_url`: strict https, a host, no `..` in the path,
/// and trailing slashes stripped; anything else is rejected and logged (never
/// the URL itself logged).
pub fn sanitize_tenant_url(candidate: &str) -> Option<String> {
    let stripped = candidate.trim().trim_end_matches('/');
    let Ok(parsed) = url::Url::parse(stripped) else {
        tracing::warn!("Rejecting tenant URL: unparsable value");
        return None;
    };
    if parsed.scheme() != "https" || parsed.host_str().is_none() {
        tracing::warn!("Rejecting tenant URL: expected https origin");
        return None;
    }
    // Url::parse normalizes '..' in paths (unlike Python's urlparse), so
    // check the raw string between host and query/fragment.
    if let Some(scheme_end) = stripped.find("://").map(|i| i + 3) {
        let after_host = &stripped[scheme_end..];
        if let Some(path_start) = after_host.find('/') {
            let path_and_rest = &after_host[path_start..];
            let raw_path = path_and_rest
                .split(['?', '#'])
                .next()
                .unwrap_or(path_and_rest);
            if raw_path.contains("..") {
                tracing::warn!("Rejecting tenant URL: path contains '..'");
                return None;
            }
        }
    }
    Some(stripped.to_owned())
}
