//! Tool-approval callback wire types.

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApprovalCallback {
    #[serde(default)]
    pub callback_id: String,
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub detail: ApprovalDetail,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApprovalDetail {
    #[serde(default)]
    pub effect: ApprovalEffect,
    #[serde(default)]
    pub required_permissions: Vec<RequiredPermission>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApprovalEffect {
    #[serde(default)]
    pub tool_name: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub input: Value,
    #[serde(default)]
    pub display: ApprovalDisplay,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApprovalDisplay {
    #[serde(default)]
    pub status_text: String,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct RequiredPermission {
    #[serde(default)]
    pub label: String,
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalDecisionType {
    Approve,
    ApproveForSession,
    ApprovePermanently,
    Deny,
}
