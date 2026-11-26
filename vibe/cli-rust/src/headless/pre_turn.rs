//! What the run settles between the session's open and its turn.

use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::{json, Value};
use tokio::sync::mpsc;

use super::drive::WorkEnd;
use super::usage::UsageBaseline;
use crate::cli::{HeadlessOptions, HeadlessSession, OutputFormat};
use crate::headless_run::StopRequests;
use crate::headless_trust::untrusted_warning;
use crate::run_export::{RunOutcome, RunWarning};
use crate::server::{
    method, notification, Client, ConfigEffectiveReadParams, Notification, RuntimeSnapshot,
};

/// The `warning` code of a connector or MCP server that failed to load.
const INTEGRATIONS_UNAVAILABLE: &str = "integrations_unavailable";

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

/// Report the config warnings and issues the session started with, and return
/// the warnings for the export, which gets the issues from the final runtime read.
pub(super) async fn config_warnings(
    client: &Arc<Client>,
    options: &HeadlessOptions,
    session_id: &str,
) -> Vec<RunWarning> {
    let runtime = match client
        .request(method::RUNTIME_READ, json!({ "sessionId": session_id }))
        .await
    {
        Ok(runtime) => runtime,
        Err(error) => {
            tracing::warn!("runtime/read for config warnings failed: {error:#}");
            return Vec::new();
        }
    };
    let (warnings, issues) = startup_config_notices(&runtime);
    for issue in &issues {
        report_warning(&options.output, issue);
    }
    warnings
        .into_iter()
        .map(|message| {
            report_warning(&options.output, &message);
            RunWarning {
                message,
                source: None,
            }
        })
        .collect()
}

/// The runtime snapshot's config validation warnings, and its config issues as
/// `file: message` lines.
fn startup_config_notices(runtime: &Value) -> (Vec<String>, Vec<String>) {
    let runtime = RuntimeSnapshot::read(runtime).unwrap_or_default();
    let issues = runtime
        .issues
        .into_iter()
        .map(|issue| match issue.file.is_empty() {
            true => issue.message,
            false => format!("{}: {}", issue.file, issue.message),
        });
    let warnings = runtime.config.unwrap_or_default().validation_warnings;
    (warnings, issues.collect())
}

/// A warning the run goes on despite: in the log, and on stderr unless the run is silent.
pub(super) fn report_warning(format: &OutputFormat, warning: &str) {
    tracing::warn!("{warning}");
    if *format != OutputFormat::Silent {
        eprintln!("{warning}");
    }
}

/// Whether `notification` names a connector or MCP server the session could not load.
pub(super) fn is_integrations_unavailable(notification: &Notification) -> bool {
    notification.method == notification::WARNING
        && notification
            .params
            .pointer("/warning/code")
            .and_then(Value::as_str)
            == Some(INTEGRATIONS_UNAVAILABLE)
}

/// The connectors and MCP servers the session could not load, reported as run warnings.
pub(super) fn integrations_unavailable(
    format: &OutputFormat,
    settled: &[Notification],
) -> Vec<RunWarning> {
    settled
        .iter()
        .filter(|notification| is_integrations_unavailable(notification))
        .filter_map(|notification| {
            notification
                .params
                .pointer("/warning/message")
                .and_then(Value::as_str)
        })
        .map(|message| {
            report_warning(format, message);
            RunWarning {
                message: message.to_owned(),
                source: Some(INTEGRATIONS_UNAVAILABLE.to_owned()),
            }
        })
        .collect()
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

#[cfg(test)]
mod tests;
