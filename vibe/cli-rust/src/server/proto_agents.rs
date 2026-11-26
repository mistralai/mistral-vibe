//! Agent and approval policy wire types (app-server `AgentSummary`, `ApprovalPolicySummary`, `AgentSafety`, `AgentType`).

use serde::{Deserialize, Serialize};

/// How much an approval policy lets run without asking (app-server `AgentSafety`).
/// Unknown wire values read as `Neutral`, so a newer server never breaks the UI.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(from = "String", rename_all = "lowercase")]
pub enum AgentSafety {
    Safe,
    #[default]
    Neutral,
    Destructive,
    Smart,
    Yolo,
}

impl From<String> for AgentSafety {
    fn from(value: String) -> Self {
        match value.as_str() {
            "safe" => Self::Safe,
            "destructive" => Self::Destructive,
            "smart" => Self::Smart,
            "yolo" => Self::Yolo,
            _ => Self::Neutral,
        }
    }
}

/// Primary agents are listed by `/agent`; subagents never are (app-server `AgentType`).
/// Unknown wire values read as `Other` and stay out of the list.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(from = "String", rename_all = "lowercase")]
pub enum AgentType {
    #[default]
    Agent,
    Subagent,
    Other,
}

impl From<String> for AgentType {
    fn from(value: String) -> Self {
        match value.as_str() {
            "agent" => Self::Agent,
            "subagent" => Self::Subagent,
            _ => Self::Other,
        }
    }
}

/// One selectable agent from the runtime snapshot (app-server `AgentSummary`).
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSummary {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub agent_type: AgentType,
}

/// One approval policy from the runtime snapshot (app-server `ApprovalPolicySummary`).
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ApprovalPolicySummary {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub safety: AgentSafety,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSwitchParams {
    pub session_id: String,
    pub agent_name: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ApprovalPolicySwitchParams {
    pub session_id: String,
    pub approval_policy: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentInstallParams {
    pub session_id: String,
    pub agent_name: String,
}

/// The agents after an install or uninstall, which may switch the active one.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentsListResponse {
    pub active: AgentSummary,
    pub agents: Vec<AgentSummary>,
    #[serde(default)]
    pub active_approval_policy: Option<ApprovalPolicySummary>,
    #[serde(default)]
    pub approval_policies: Option<Vec<ApprovalPolicySummary>>,
}
