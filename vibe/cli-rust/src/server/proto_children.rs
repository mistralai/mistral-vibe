//! Child-session wire types (app-server `PublicChildSession` and its status union).

use serde::Deserialize;
use serde_json::Value;

use super::types::TokenUsage;

/// Session status union (app-server `PublicSessionStatus`). Unknown wire tags read
/// as `Unknown`, so a newer server never breaks the client (ADR 0014).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum SessionStatus {
    Running,
    Blocked,
    Failed,
    Archived,
    Idle,
    #[default]
    Unknown,
}

impl SessionStatus {
    fn from_tag(tag: &str) -> Self {
        match tag {
            "running" => Self::Running,
            "blocked" => Self::Blocked,
            "failed" => Self::Failed,
            "archived" => Self::Archived,
            "idle" => Self::Idle,
            _ => Self::Unknown,
        }
    }
}

impl<'de> Deserialize<'de> for SessionStatus {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = Value::deserialize(deserializer)?;
        Ok(match value.get("type").and_then(Value::as_str) {
            Some(tag) => Self::from_tag(tag),
            None => Self::Unknown,
        })
    }
}

/// The model a child's agent type declares (app-server `PublicChildModel`).
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChildModel {
    pub display_name: String,
    pub thinking: String,
}

impl ChildModel {
    /// `name[thinking]`, as the banner names the session's model.
    pub fn label(&self) -> String {
        format!("{}[{}]", self.display_name, self.thinking)
    }
}

/// A child (subagent) session (app-server `PublicChildSession`). Required fields
/// have no default, so a malformed child fails its own parse.
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicChildSession {
    pub id: String,
    pub name: String,
    pub agent_type: String,
    pub status: SessionStatus,
    /// The model its agent type declares; `None` runs on the session's.
    #[serde(default)]
    pub model: Option<ChildModel>,
    #[serde(default)]
    pub token_usage: TokenUsage,
    #[serde(default)]
    pub context_usage: Option<TokenUsage>,
    pub created_at: i64,
    pub updated_at: i64,
}

impl PublicChildSession {
    /// Tokens shown in the status row: the context usage when present, else 0.
    pub fn context_tokens(&self) -> u64 {
        match &self.context_usage {
            Some(usage) => usage.total_tokens,
            None => 0,
        }
    }
}

/// `session/childSessionUpdated` params (app-server `ChildSessionUpdatedParams`).
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChildSessionUpdatedParams {
    pub child_session: PublicChildSession,
}
