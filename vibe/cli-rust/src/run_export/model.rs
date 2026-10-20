//! The export's data models, following the app server's `run_export.py` contract.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The one taxonomy for how a headless run ended. Each maps to one process exit code:
/// - 0: `Finished`
/// - 1: `UsageError`, `ConfigError`
/// - 2: `InfrastructureFailure`
/// - 3: `TurnLimit`, `TokenLimit`, `PriceLimit`, `Deadline`, `Terminated`, `Length`, `Refusal`
/// - 4: `Aborted`, by the server on the other end of `--agent-socket`
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunOutcome {
    Finished,
    UsageError,
    ConfigError,
    InfrastructureFailure,
    TurnLimit,
    TokenLimit,
    PriceLimit,
    Deadline,
    Terminated,
    Length,
    Refusal,
    Aborted,
}

impl RunOutcome {
    pub fn exit_code(self) -> u8 {
        match self {
            Self::Finished => 0,
            Self::UsageError | Self::ConfigError => 1,
            Self::InfrastructureFailure => 2,
            Self::TurnLimit
            | Self::TokenLimit
            | Self::PriceLimit
            | Self::Deadline
            | Self::Terminated
            | Self::Length
            | Self::Refusal => 3,
            Self::Aborted => 4,
        }
    }
}

impl std::fmt::Display for RunOutcome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Self::Finished => "finished",
            Self::UsageError => "usage_error",
            Self::ConfigError => "config_error",
            Self::InfrastructureFailure => "infrastructure_failure",
            Self::TurnLimit => "turn_limit",
            Self::TokenLimit => "token_limit",
            Self::PriceLimit => "price_limit",
            Self::Deadline => "deadline",
            Self::Terminated => "terminated",
            Self::Length => "length",
            Self::Refusal => "refusal",
            Self::Aborted => "aborted",
        };
        f.write_str(s)
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunError {
    pub message: String,
    pub code: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RunUsage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cached_input_tokens: u64,
    pub total_tokens: u64,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct RunLimits {
    pub max_turns: Option<u32>,
    pub max_price: Option<f64>,
    pub max_tokens: Option<u64>,
    pub time_limit_s: Option<f64>,
}

/// Something the run went without, or worked around.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunWarning {
    pub message: String,
    pub source: Option<String>,
}

/// How a headless run ended, as far as the run itself can tell.
/// Field names and nulls match the app server's `RunResult`: the export is the contract.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunResult {
    pub outcome: RunOutcome,
    pub stop_reason: Option<TurnStop>,
    pub error: Option<RunError>,
    pub session_id: Option<String>,
    pub usage: Option<RunUsage>,
    pub cost_usd: Option<f64>,
    pub steps: Option<u64>,
    pub config: Option<Value>,
    /// Left out of the export when there are none.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub warnings: Option<Vec<RunWarning>>,
}

/// Where a subagent was when the run ended: `Interrupted` is idle after an
/// interrupted turn, `Unknown` a state this version does not know.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunSubagentStatus {
    Running,
    Idle,
    Interrupted,
    Failed,
    Closed,
    #[serde(other)]
    Unknown,
}

/// One subagent the session spawned. `usage`, `cost_usd` and `steps` are its
/// own, and the run's count none of them. `model` is the provider model its
/// agent called, if exactly one. `cost_usd` is priced at the rates of
/// `pricing_model`, a configured alias: its own model's, or the root
/// session's when its model is unknown, as `pricing_fallback` says.
/// `journal_dir` is relative to the output directory.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RunSubagent {
    pub session_id: String,
    pub parent_session_id: String,
    pub root_session_id: String,
    pub agent_name: String,
    pub agent_type: Option<String>,
    pub spawn_action_id: String,
    pub spawn_call_id: Option<String>,
    pub status: RunSubagentStatus,
    #[serde(default)]
    pub model: Option<String>,
    pub usage: Option<RunUsage>,
    pub cost_usd: Option<f64>,
    #[serde(default)]
    pub pricing_model: Option<String>,
    #[serde(default)]
    pub pricing_fallback: bool,
    pub steps: Option<u64>,
    pub journal_dir: Option<String>,
}

/// A subagent as the run read it, with the store its journal is copied from.
#[derive(Clone, Debug)]
pub struct SubagentReport {
    pub subagent: RunSubagent,
    pub session_dir: Option<PathBuf>,
}

/// The full export written to `export.json`.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunExport {
    #[serde(flatten)]
    pub result: RunResult,
    pub schema_version: u32,
    pub vibe_version: String,
    pub journal_dir: Option<String>,
    pub started_at: String,
    pub ended_at: String,
    pub limits: RunLimits,
    /// Computed from the outcome.
    pub exit_code: u8,
    /// Left out of the export when the session spawned none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subagents: Option<Vec<RunSubagent>>,
}

/// The report a headless run produces: the result plus the final response
/// and session directory for export purposes.
#[derive(Clone, Debug)]
pub struct RunReport {
    pub result: RunResult,
    pub final_response: Option<String>,
    pub session_dir: Option<PathBuf>,
    pub subagents: Vec<SubagentReport>,
}

/// Why a Turn ended before the agent finished, read one way for every client.
/// Wire values of the app server's `TurnStop` in `vibe/app_server/models.py`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TurnStop {
    Interrupted,
    Limit,
    Length,
}

impl std::fmt::Display for TurnStop {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Self::Interrupted => "interrupted",
            Self::Limit => "limit",
            Self::Length => "length",
        };
        f.write_str(s)
    }
}

/// Read a `TurnStop` from a turn-completed notification's turn object.
/// An interrupted status wins over the stop reason.
pub fn turn_stop_from_turn(turn: &Value) -> Option<TurnStop> {
    let status = turn.get("status").and_then(Value::as_str)?;
    if status == "interrupted" {
        return Some(TurnStop::Interrupted);
    }
    match turn.get("stopReason").and_then(Value::as_str) {
        Some("limit") => Some(TurnStop::Limit),
        Some("length") => Some(TurnStop::Length),
        _ => None,
    }
}
