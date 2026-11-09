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
