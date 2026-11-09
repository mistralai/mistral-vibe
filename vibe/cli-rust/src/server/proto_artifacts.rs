//! Wire types for the session-artifact RPCs (app-server `artifacts/*`).

use serde::Deserialize;

/// A session artifact as sent on the wire (mirrors the app-server
/// `PublicSessionArtifact`; unknown fields are ignored for forward compat).
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicSessionArtifact {
    pub id: String,
    pub session_id: String,
    pub kind: String,
    pub provider: String,
    pub external_key: String,
    pub external_url: String,
    #[serde(default)]
    pub display_title: Option<String>,
    pub discovered_at: String,
    #[serde(default)]
    pub viewed_at: Option<String>,
    #[serde(default)]
    pub details: Option<ArtifactDetails>,
}

/// The PR/MR number fields of the provider-discriminated details union; the
/// bar only renders the number, so the rest of the union stays unread.
/// Phase 4 adds the synced enrichment surface the CLI renders: the lifecycle
/// state, the aggregated actionability status (`prStatus`), and the CI
/// checks summary. All optional — pre-Phase-4 payloads parse unchanged.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ArtifactDetails {
    #[serde(default)]
    pub pull_request_number: Option<u64>,
    #[serde(default)]
    pub merge_request_iid: Option<u64>,
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default)]
    pub pr_status: Option<String>,
    #[serde(default)]
    pub checks: Option<ChecksSummary>,
}

/// The CI checks summary (`checks` on the details): an overall state plus the
/// per-check entries rendered in the artifacts modal.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChecksSummary {
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default)]
    pub checked_at: Option<String>,
    #[serde(default)]
    pub checks: Vec<CheckStatus>,
}

/// One CI check entry.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CheckStatus {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
}

/// `artifacts/list` response.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ArtifactsListResponse {
    #[serde(default)]
    pub artifacts: Vec<PublicSessionArtifact>,
}
