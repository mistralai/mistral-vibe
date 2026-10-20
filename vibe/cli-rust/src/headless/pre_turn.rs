//! What the run settles between the session's open and its turn.

use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::Value;
use tokio::sync::mpsc;

use super::drive::WorkEnd;
use super::usage::UsageBaseline;
use crate::cli::{HeadlessOptions, HeadlessSession, OutputFormat};
use crate::headless_run::StopRequests;
use crate::headless_trust::untrusted_warning;
use crate::run_export::{RunOutcome, RunWarning};
use crate::server::{method, Client, ConfigEffectiveReadParams, Notification};

/// A reopened session's earlier runs neither spend this run's budgets nor count in its export.
pub(super) async fn usage_baseline(
    client: &Arc<Client>,
    session: &HeadlessSession,
    session_id: &str,
) -> Result<UsageBaseline, WorkEnd> {
    if *session == HeadlessSession::New {
        return Ok(UsageBaseline::default());
    }
    UsageBaseline::read(client, session_id)
        .await
        .map_err(|error| WorkEnd::Failed(RunOutcome::InfrastructureFailure, format!("{error:#}")))
}

/// Warn if the workspace is untrusted, and return the warning for the export;
/// a sandboxed workspace, or a run with no host directory, has no trust state here.
pub(super) async fn host_trust_warning(
    client: &Arc<Client>,
    options: &HeadlessOptions,
    session_cwd: Option<&str>,
    cwd: Option<&str>,
) -> Result<Option<RunWarning>, WorkEnd> {
    if options.agent_socket.is_some()
        && (cwd.is_none() || crate::agent_socket::sandboxed(session_cwd, cwd))
    {
        return Ok(None);
    }
    let warning = untrusted_warning(client, cwd).await.map_err(|error| {
        WorkEnd::Failed(
            RunOutcome::InfrastructureFailure,
            format!("workspace/trust/status: {error:#}"),
        )
    })?;
    Ok(warning.map(|message| {
        report_warning(&options.output, &message);
        RunWarning {
            message,
            source: None,
        }
    }))
}

/// A warning the run goes on despite: in the log, and on stderr unless the run is silent.
pub(super) fn report_warning(format: &OutputFormat, warning: &str) {
    tracing::warn!("{warning}");
    if *format != OutputFormat::Silent {
        eprintln!("{warning}");
    }
}

/// An abort the server sent before the turn ends the run before the model is called.
pub(super) fn pending_abort(
    notifications: &mut mpsc::Receiver<Notification>,
    settled: &mut Vec<Notification>,
    stop: &StopRequests,
) -> Option<WorkEnd> {
    while let Ok(notification) = notifications.try_recv() {
        settled.push(notification);
    }
    let message = crate::agent_socket::abort_message(settled)?;
    stop.abort(&message);
    Some(WorkEnd::Failed(RunOutcome::Aborted, message))
}

/// Read the effective config via `config/effective/read`, for the export.
pub(super) async fn read_effective_config(client: &Arc<Client>, session_id: &str) -> Result<Value> {
    let params = ConfigEffectiveReadParams {
        session_id: session_id.to_owned(),
    };
    let value = serde_json::to_value(&params)?;
    let response = client
        .request(method::CONFIG_EFFECTIVE_READ, value)
        .await
        .context("config/effective/read")?;
    let config = response
        .get("config")
        .cloned()
        .context("config/effective/read response missing config")?;
    Ok(config)
}
