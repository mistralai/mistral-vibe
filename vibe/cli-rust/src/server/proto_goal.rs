//! Wire types for `/goal`: `PublicGoal`, `goal/set` and `goal/clear`.

use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

/// `GoalStatus`; an unknown status from a newer server reads as `Unknown`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GoalStatus {
    Active,
    Paused,
    Blocked,
    Complete,
    BudgetLimited,
    UsageLimited,
    #[serde(other)]
    Unknown,
}

/// The subset of the server's `PublicGoal` the client renders.
#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicGoal {
    pub goal_id: String,
    pub revision: u64,
    pub objective: String,
    pub status: GoalStatus,
    #[serde(default)]
    pub status_reason: Option<String>,
    #[serde(default)]
    pub token_budget: Option<u64>,
    #[serde(default)]
    pub tokens_used: u64,
    #[serde(default)]
    pub time_used_seconds: u64,
}

impl PublicGoal {
    /// Paused because the session closed while the goal was active.
    pub fn suspended(&self) -> bool {
        self.status == GoalStatus::Paused && self.status_reason.as_deref() == Some("suspended")
    }
}

/// `deserialize_with` reading a malformed goal as none rather than failing the session (ADR 0014).
pub fn lenient_goal<'de, D: Deserializer<'de>>(d: D) -> Result<Option<Box<PublicGoal>>, D::Error> {
    Ok(PublicGoal::deserialize(Value::deserialize(d)?)
        .ok()
        .map(Box::new))
}

/// `goal/set`: create, replace, or edit; an omitted budget keeps it, `Some(None)` removes it.
#[derive(Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GoalSetParams {
    pub session_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expected_goal_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expected_revision: Option<u64>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub replace: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub objective: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub token_budget: Option<Option<u64>>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GoalClearParams {
    pub session_id: String,
    pub expected_goal_id: String,
}
