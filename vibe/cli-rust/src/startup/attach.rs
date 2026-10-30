//! Attaching the session a `--continue`/`--resume` intent asks for.

use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::{json, Value};
use tokio::sync::mpsc;

use super::Attach;
use crate::cli::StartupResume;
use crate::server::{
    method, request_error::resume_failure_message, AgentConfig, Client, Notification,
    RequestFailure, RpcError, SessionStartParams, HISTORY_LIMIT,
};
use crate::startup::StartupEvent;
use crate::worktree::WorktreeInfo;

/// Why `session/start` failed, for the handshake to classify.
pub(super) enum StartError {
    /// Auth: missing API key (`unauthorized`).
    Auth(Box<RpcError>),
    /// Invalid config (`invalid_params` with `kind == "configuration"`).
    Config(Box<RpcError>),
    /// Any other transport or protocol error.
    Other(anyhow::Error),
}

/// Attach the session to work in. `session/start` runs first even for a resume:
/// it brings the engine up, and resuming onto that warm loop is a rebind (tens
/// of milliseconds) where resuming cold pays the bring-up all over again.
pub(super) async fn attach_session(
    client: &Arc<Client>,
    resume: StartupResume,
    agent_config: AgentConfig,
    list_error: Option<String>,
) -> Result<(Value, Attach), StartError> {
    let started = start_session(client, agent_config.clone()).await?;
    rebind(client, started, resume, &agent_config, list_error)
        .await
        .map_err(StartError::Other)
}

/// Start the fresh session a resume rebinds onto.
pub(super) async fn start_session(
    client: &Arc<Client>,
    agent_config: AgentConfig,
) -> Result<Value, StartError> {
    let start = SessionStartParams {
        agent_config,
        history_limit: HISTORY_LIMIT,
        model: None,
        session_only: false,
    };
    let params = serde_json::to_value(&start).map_err(|e| StartError::Other(e.into()))?;
    let result = client
        .request_err(method::SESSION_START, params)
        .await
        .map_err(|err| {
            if err.is_unauthorized() {
                StartError::Auth(Box::new(err))
            } else if err.is_config_error() {
                StartError::Config(Box::new(err))
            } else {
                StartError::Other(anyhow::anyhow!(
                    "session/start failed: [{}] {}",
                    err.code(),
                    err.message
                ))
            }
        })?;
    result
        .get("state")
        .cloned()
        .with_context(|| "session/start response missing state")
        .map_err(StartError::Other)
}

/// Rebind onto the saved session the resume intent names, or keep the fresh
/// one when there is nothing to resume (the rebind never fails the startup).
pub(super) async fn rebind(
    client: &Arc<Client>,
    started: Value,
    resume: StartupResume,
    agent_config: &AgentConfig,
    list_error: Option<String>,
) -> Result<(Value, Attach)> {
    let (method, params) = match (resume, list_error) {
        (_, Some(error)) => return Ok((started, Attach::Failed(error))),
        (StartupResume::ById(id), None) => {
            (method::SESSION_RESUME, resume_params(&id, agent_config))
        }
        // The server picks the session: this terminal's last one in the folder, else the latest.
        (StartupResume::Continue, None) => {
            (method::SESSION_CONTINUE, continue_params(agent_config))
        }
        _ => return Ok((started, Attach::Started)),
    };
    // The full agent config rides on resume too, so flags survive the rebind.
    match state_of(client, method, params).await {
        Ok(state) => {
            let id = state
                .pointer("/session/id")
                .and_then(Value::as_str)
                .context("reopened session state missing session id")?
                .to_owned();
            Ok((state, Attach::Resumed(id)))
        }
        // `--continue` with nothing to continue keeps the fresh session.
        Err(error) if nothing_to_continue(&error) => Ok((started, Attach::NoPrevious)),
        // The fresh session is already attached, so a failed resume keeps it.
        Err(error) => Ok((started, Attach::Failed(resume_failure_message(&error)))),
    }
}

/// The `harnessCode` of the `session/continue` error when no session matches.
const NOTHING_TO_CONTINUE: &str = "nothing_to_continue";

/// Whether `session/continue` found no session, as opposed to failing.
pub fn nothing_to_continue(error: &anyhow::Error) -> bool {
    matches!(
        error.downcast_ref::<RequestFailure>(),
        Some(RequestFailure::Rpc(rpc)) if rpc.harness_code() == Some(NOTHING_TO_CONTINUE)
    )
}

/// `session/resume` params; the flags ride along.
pub fn resume_params(id: &str, agent_config: &AgentConfig) -> Value {
    json!({
        "sessionId": id,
        "agentConfig": saved_session_config(agent_config),
        "historyLimit": HISTORY_LIMIT,
    })
}

/// `session/continue` params; the flags ride along.
pub fn continue_params(agent_config: &AgentConfig) -> Value {
    json!({
        "agentConfig": saved_session_config(agent_config),
        "historyLimit": HISTORY_LIMIT,
    })
}

/// The agent config without `worktree`, which the server only takes on `session/start`.
fn saved_session_config(agent_config: &AgentConfig) -> Value {
    let mut config = serde_json::to_value(agent_config).expect("serialize agent config");
    if let Some(fields) = config.as_object_mut() {
        fields.remove("worktree");
    }
    config
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

/// A worktree run waits its worktree out before anything that depends on
/// where the session runs: the settle carries the directory the resume
/// scopes to, and the runtime reads follow the rebind. Returns the rebound
/// state, the settle's cwd and worktree, and the notifications to replay.
#[allow(clippy::too_many_arguments)]
pub(super) async fn settle_worktree_session(
    client: &Arc<Client>,
    event_tx: &mpsc::Sender<StartupEvent>,
    notifications: Option<(mpsc::Receiver<Notification>, mpsc::Sender<Notification>)>,
    resume: &StartupResume,
    agent_config: &AgentConfig,
    mut list_error: Option<String>,
    state: Value,
) -> Result<(
    Value,
    Attach,
    Option<String>,
    Option<WorktreeInfo>,
    Vec<Notification>,
)> {
    let (mut stream, bridge) =
        notifications.context("a worktree run hands its notification stream to the handshake")?;
    let deadline = tokio::time::Instant::now() + crate::worktree_gate::SETTLE_WAIT;
    let (settle, waited) = crate::worktree_gate::absorb_until_settled(&mut stream, deadline)
        .await
        .map_err(anyhow::Error::msg)?;
    let settled = settle.cwd;
    let prepared = settle.worktree;
    let absorbed = waited.notifications;
    // The event loop reads the bridge; everything past the settle flows on.
    tokio::spawn(async move {
        while let Some(notification) = stream.recv().await {
            if bridge.send(notification).await.is_err() {
                break;
            }
        }
    });
    if matches!(resume, StartupResume::Picker) {
        match client
            .request(method::SESSION_LIST, json!({ "cwd": settled }))
            .await
        {
            Ok(value) => event_tx
                .send(StartupEvent::Sessions(Box::new(value)))
                .await
                .context("startup event receiver closed")?,
            Err(error) => list_error = Some(format!("Failed to list sessions: {error}")),
        }
    }
    // The rebind's agentConfig names the worktree directory, not the checkout
    // the run started in; the settled cwd is that directory.
    let mut rebound_config = agent_config.clone();
    if let Some(settled) = settled.as_ref() {
        rebound_config.cwd = Some(settled.clone());
    }
    let (state, attach) =
        rebind(client, state, resume.clone(), &rebound_config, list_error).await?;
    Ok((state, attach, settled, prepared, absorbed))
}
