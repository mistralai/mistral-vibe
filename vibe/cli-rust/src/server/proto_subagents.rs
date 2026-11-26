//! Experimental `session/subagents/*` wire types (app-server `protocol.py`).

use serde::Deserialize;

/// `session/subagents/wait` result: `settled` is false when the timeout ran out first.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct SubagentsWaitResponse {
    pub settled: bool,
}

/// One running subagent's interrupt: `outcome` is `interrupted`, `notRunning` or `failed`.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SubagentInterruptResult {
    pub session_id: String,
    pub name: String,
    pub outcome: String,
    #[serde(default)]
    pub error: Option<String>,
}

/// `session/subagents/interrupt` result.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct SubagentsInterruptResponse {
    #[serde(default)]
    pub results: Vec<SubagentInterruptResult>,
}

/// A subagent's own token counts.
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct SubagentUsage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cached_input_tokens: u64,
    pub total_tokens: u64,
}

/// One subagent of the session (app-server `SubagentRun`). `cost_usd` is priced at
/// `pricing_model`'s rates: its own model's, or the root's when `pricing_fallback`.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SubagentRun {
    pub session_id: String,
    pub parent_session_id: String,
    pub root_session_id: String,
    pub name: String,
    #[serde(default)]
    pub agent_type: Option<String>,
    pub spawn_action_id: String,
    #[serde(default)]
    pub spawn_call_id: Option<String>,
    pub status: String,
    #[serde(default)]
    pub session_dir: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub usage: Option<SubagentUsage>,
    #[serde(default)]
    pub cost_usd: Option<f64>,
    #[serde(default)]
    pub pricing_model: Option<String>,
    #[serde(default)]
    pub pricing_fallback: bool,
    #[serde(default)]
    pub steps: Option<u64>,
}

/// `session/subagents/read` result, in spawn order.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct SubagentsReadResponse {
    #[serde(default)]
    pub subagents: Vec<SubagentRun>,
}
