//! The headless `agentConfig` sent with `session/start`.

use anyhow::{Context, Result};
use serde_json::Value;

use crate::cli::HeadlessOptions;
use crate::server::{AgentConfig, SessionStartParams};
use crate::utils::paths::resolve_add_dirs;

/// Headless `agentConfig`: token and price budgets are enforced client-side
/// by StopRequests, not sent as session options.
pub fn agent_config(
    options: &HeadlessOptions,
    cwd: Option<String>,
    terminal_key: Option<String>,
) -> Result<AgentConfig> {
    Ok(AgentConfig {
        cwd,
        agent: options.agent.clone(),
        auto_approve: options.auto_approve,
        enabled_tools: options.enabled_tools.clone(),
        disabled_tools: build_disabled_tools(&options.disabled_tools),
        // max_turns stays server-side; only the token and price budgets move client-side.
        max_turns: options.max_turns,
        // Token and price budgets are enforced client-side via StopRequests.
        max_price: None,
        max_session_tokens: None,
        system_prompt: options.system_prompt.clone(),
        headless: true,
        trust_workspace: options.trust,
        workspace_roots: resolve_add_dirs(&options.add_dir)?,
        worktree: options.worktree.clone(),
        // An agent-socket run works in a sandbox, not in this terminal's folder.
        terminal_key: terminal_key.filter(|_| options.agent_socket.is_none()),
    })
}

/// `session/start` params; `--model` applies to this session only.
pub fn start_params(
    options: &HeadlessOptions,
    agent_config: AgentConfig,
    history_limit: u32,
) -> SessionStartParams {
    SessionStartParams {
        agent_config,
        history_limit,
        model: options.model.clone(),
        session_only: options.model.is_some(),
    }
}

pub fn build_disabled_tools(explicit: &[String]) -> Vec<String> {
    let mut tools = explicit.to_vec();
    // A headless run has no one to answer a question, and no scheduler for
    // `cron` to hand its jobs to.
    for &t in &["ask_user_question", "cron"] {
        if !tools.iter().any(|x| x == t) {
            tools.push(t.to_owned());
        }
    }
    tools
}

/// A missing or non-string id must fail the run, not default to "" and hang.
pub fn turn_id_from(response: &Value) -> Result<String> {
    response
        .pointer("/turn/id")
        .and_then(Value::as_str)
        .context("turn/start response missing turn id")
        .map(str::to_owned)
}
