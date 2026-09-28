//! Attaching the session a `--continue`/`--resume` intent asks for.

use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::{json, Value};

use super::Attach;
use crate::cli::StartupResume;
use crate::server::{method, AgentConfig, Client, SessionStartParams, HISTORY_LIMIT};

/// Attach the session to work in. `session/start` runs first even for a resume:
/// it brings the engine up, and resuming onto that warm loop is a rebind (tens
/// of milliseconds) where resuming cold pays the bring-up all over again.
pub(super) async fn attach_session(
    client: &Arc<Client>,
    resume: StartupResume,
    agent_config: AgentConfig,
    continue_target: Option<String>,
    list_error: Option<String>,
) -> Result<(Value, Attach)> {
    let target = resume.target(continue_target);
    let started = start_session(client, agent_config.clone()).await?;
    let Some(id) = target else {
        // `--continue` with nothing to continue keeps the fresh session.
        let attach = match (resume, list_error) {
            (_, Some(error)) => Attach::Failed(error),
            (StartupResume::Continue, None) => Attach::NoPrevious,
            _ => Attach::Started,
        };
        return Ok((started, attach));
    };
    // Python sends the full `SessionOptions` on resume too, so flags survive the rebind.
    let params = resume_params(&id, &agent_config);
    match state_of(client, method::SESSION_RESUME, params).await {
        Ok(state) => Ok((state, Attach::Resumed(id))),
        // The fresh session is already attached, so a failed resume keeps it.
        Err(error) => Ok((
            started,
            Attach::Failed(format!("Failed to resume session: {error:#}")),
        )),
    }
}

async fn start_session(client: &Arc<Client>, agent_config: AgentConfig) -> Result<Value> {
    let start = SessionStartParams {
        agent_config,
        history_limit: HISTORY_LIMIT,
    };
    state_of(client, method::SESSION_START, serde_json::to_value(&start)?).await
}

/// `session/resume` params; the flags ride along, as in Python `SessionOptions`.
pub fn resume_params(id: &str, agent_config: &AgentConfig) -> Value {
    json!({
        "sessionId": id,
        "agentConfig": agent_config,
        "historyLimit": HISTORY_LIMIT,
    })
}

/// The `state` of a session lifecycle answer.
async fn state_of(client: &Arc<Client>, method: &str, params: Value) -> Result<Value> {
    client
        .request(method, params)
        .await
        .with_context(|| method.to_owned())?
        .get("state")
        .cloned()
        .with_context(|| format!("{method} response missing state"))
}
