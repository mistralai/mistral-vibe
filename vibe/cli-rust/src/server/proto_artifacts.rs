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

/// The PR/MR number fields of the provider-discriminated details union, plus
/// the synced state, status and CI checks the CLI renders. All optional, so
/// older payloads parse unchanged.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ArtifactDetails {
    #[serde(default)]
    pub pull_request_number: Option<u64>,
    #[serde(default)]
    pub merge_request_iid: Option<u64>,
    #[serde(default)]
    pub state: Option<PrState>,
    #[serde(default)]
    pub pr_status: Option<PrStatus>,
    /// Why `pr_status` is `action_needed` or `waiting`, decided by the server.
    #[serde(default)]
    pub pr_status_reason: Option<PrStatusReason>,
    #[serde(default)]
    pub checks: Option<ChecksSummary>,
}

/// A PR/MR's lifecycle state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PrState {
    Open,
    Draft,
    Merged,
    Closed,
    #[serde(other)]
    Unknown,
}

/// The server's actionability status of an open PR/MR.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PrStatus {
    ActionNeeded,
    Waiting,
    Mergeable,
    #[serde(other)]
    Unknown,
}

/// The cause of a `PrStatus`, in the server's precedence order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PrStatusReason {
    ChangesRequested,
    ChecksFailing,
    Conflicts,
    ChecksPending,
    ReviewRequired,
    MergeBlocked,
    #[serde(other)]
    Unknown,
}

/// One CI check's outcome.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CheckOutcome {
    Pass,
    Fail,
    Pending,
    #[serde(other)]
    Unknown,
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
    pub status: Option<CheckOutcome>,
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
