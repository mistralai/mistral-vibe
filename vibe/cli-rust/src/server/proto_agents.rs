//! Agent wire types (Python `AgentSummary`, `AgentSafety`, `AgentType`).

use serde::{Deserialize, Serialize};

/// How much a profile is allowed to do without asking (Python `AgentSafety`).
/// Unknown wire values read as `Neutral`, so a newer server never breaks the UI.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(from = "String", rename_all = "lowercase")]
pub enum AgentSafety {
    Safe,
    #[default]
    Neutral,
    Destructive,
    Yolo,
}

impl From<String> for AgentSafety {
    fn from(value: String) -> Self {
        match value.as_str() {
            "safe" => Self::Safe,
            "destructive" => Self::Destructive,
            "yolo" => Self::Yolo,
            _ => Self::Neutral,
        }
    }
}

/// Primary agents cycle with Shift+Tab; subagents never do (Python `AgentType`).
/// Unknown wire values read as `Other` and stay out of the cycle.
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

/// One selectable agent from the runtime snapshot (Python `AgentSummary`).
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
    pub safety: AgentSafety,
    #[serde(default)]
    pub agent_type: AgentType,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentSwitchParams {
    pub session_id: String,
    pub agent_name: String,
}
