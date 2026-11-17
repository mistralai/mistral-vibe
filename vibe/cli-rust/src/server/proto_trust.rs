//! Wire types for the workspace-trust gate.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceTrustStatusParams {
    pub cwd: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceTrustStatusResponse {
    #[serde(default)]
    pub details: Option<WorkspaceTrustDetails>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceTrustDetails {
    pub cwd: String,
    #[serde(default)]
    pub repo_root: Option<String>,
    #[serde(default)]
    pub detected_files: Vec<String>,
    #[serde(default)]
    pub repo_detected_files: Vec<String>,
    #[serde(default)]
    pub repo_explicitly_untrusted: bool,
    #[serde(default)]
    pub settings_path: String,
    #[serde(default)]
    pub available_decisions: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceTrustDecisionParams {
    pub decision: String,
    pub cwd: Option<String>,
    /// Always absent here: the gate answers before any session exists.
    pub session_id: Option<String>,
}

/// `workspace/trust/folders/list` and `/update` response: the persisted decisions.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceTrustFolders {
    #[serde(default)]
    pub trusted: Vec<String>,
    #[serde(default)]
    pub untrusted: Vec<String>,
}

/// One folder `workspace/trust/folders/inspect` offers to trust.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TrustFolderCandidate {
    /// Absolute and resolved: what `workspace/trust/folders/add` stores.
    pub path: String,
    /// The field text that selects it.
    pub value: String,
    /// `home` or `root`: the folder cannot be trusted.
    #[serde(default)]
    pub problem: Option<String>,
    /// `trusted` or `untrusted` when the folder is already stored.
    #[serde(default)]
    pub stored: Option<String>,
    #[serde(default)]
    pub trusted_by: Option<String>,
    #[serde(default)]
    pub untrusted_inside: Vec<String>,
    #[serde(default)]
    pub untrusted_inside_count: usize,
}

/// `workspace/trust/folders/inspect` response: the typed folder or its closest parent, then completions.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TrustFolderInspection {
    /// `missing` or `not_directory` when the typed path names no folder.
    #[serde(default)]
    pub typed_problem: Option<String>,
    #[serde(default)]
    pub candidates: Vec<TrustFolderCandidate>,
}
