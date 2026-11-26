//! Opening the run's session: a new one, or a saved one continued or resumed.

use std::sync::Arc;

use anyhow::Context;
use serde_json::Value;

use super::config::{agent_config, start_params};
use super::drive::WorkEnd;
use crate::cli::{HeadlessOptions, HeadlessSession};
use crate::run_export::RunOutcome;
use crate::server::{method, Client, RpcError};
use crate::startup::{continue_params, resume_params};

/// The headless run's session history page, as on `session/start`.
const HISTORY_LIMIT: u32 = 200;

/// Open the run's session: `session/start` for a new one, `session/continue`
/// or `session/resume` for a saved one. A failure is classified for the
/// export.
pub(super) async fn open_session(
    client: &Arc<Client>,
    options: &HeadlessOptions,
    cwd: Option<String>,
    session: &HeadlessSession,
) -> Result<(String, Option<String>), WorkEnd> {
    let infrastructure = |error: anyhow::Error| {
        WorkEnd::Failed(RunOutcome::InfrastructureFailure, format!("{error:#}"))
    };
    let config =
        agent_config(options, cwd, crate::terminal_key::current()).map_err(infrastructure)?;
    let (lifecycle, params) = match session {
        HeadlessSession::New => {
            let params = start_params(options, config, HISTORY_LIMIT);
            let params = serde_json::to_value(&params).map_err(|e| infrastructure(e.into()))?;
            (method::SESSION_START, params)
        }
        HeadlessSession::Continue => (method::SESSION_CONTINUE, continue_params(&config)),
        HeadlessSession::Resume(id) => (method::SESSION_RESUME, resume_params(id, &config)),
    };
    let result = client
        .request_err(lifecycle, params)
        .await
        .map_err(|error| WorkEnd::Failed(open_failure(&error), error.message))?;
    let session = result.pointer("/state/session");
    let field = |name: &str| session.and_then(|s| s.get(name)).and_then(Value::as_str);
    let id = field("id")
        .map(str::to_owned)
        .with_context(|| format!("{lifecycle} response missing session id"))
        .map_err(infrastructure)?;
    Ok((id, field("cwd").map(str::to_owned)))
}

/// The outcome of a session that did not open: a missing API key or a config
/// that does not load is the config's fault, a request the server cannot
/// serve (an unknown session, nothing to continue) the caller's, and a failed
/// `--agent-socket` handshake as the server names it.
pub fn open_failure(error: &RpcError) -> RunOutcome {
    if let Some(outcome) = crate::agent_socket::named_outcome(error) {
        return outcome;
    }
    if error.is_unauthorized() || error.is_config_error() {
        return RunOutcome::ConfigError;
    }
    match error.code().as_str() {
        "invalid_params" | "not_found" => RunOutcome::UsageError,
        _ => RunOutcome::InfrastructureFailure,
    }
}
