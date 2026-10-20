//! The run's subagents: waited for after its turn, interrupted when it stops,
//! and read for its report.

use std::path::PathBuf;
use std::time::Duration;

use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio::time::Instant;

use crate::headless_run::StopRequests;
use crate::run_export::{RunSubagent, RunSubagentStatus, RunUsage, SubagentReport};
use crate::server::{
    method, notification, Client, Notification, SubagentInterruptResult, SubagentRun,
    SubagentsInterruptResponse, SubagentsReadResponse, SubagentsWaitResponse,
};

/// One `session/subagents/wait` request's timeout; the run waits as many as it takes.
const WAIT_SLICE_MS: u64 = 60_000;
/// The server's cap on `session/subagents/read`'s timeout.
const READ_TIMEOUT_CAP_MS: u64 = 600_000;
/// Left of the grace for `session/stop` once the subagents are read.
const READ_MARGIN: Duration = Duration::from_secs(1);

/// Wait for the subagents the turn left running, so the export sees how each
/// one ended. The session takes no further turn: a late result stays queued
/// for it. A stop interrupts them; a server abort still stops the run.
pub async fn wait_for_subagents(
    client: &Client,
    notifications: &mut mpsc::Receiver<Notification>,
    session_id: &str,
    stop: &StopRequests,
) {
    loop {
        let params = json!({ "sessionId": session_id, "timeoutMs": WAIT_SLICE_MS });
        let wait = client.request(method::SESSION_SUBAGENTS_WAIT, params);
        tokio::pin!(wait);
        let response = loop {
            tokio::select! {
                response = &mut wait => break response,
                Some(notification) = notifications.recv() => {
                    if notification.method == notification::AGENT_SOCKET_ABORTED {
                        let message = notification.params.get("message").and_then(Value::as_str);
                        stop.abort(message.unwrap_or_default());
                    }
                }
            }
        };
        let settled = response.and_then(|value| Ok(serde_json::from_value(value)?));
        match settled {
            Ok(SubagentsWaitResponse { settled: true }) => return,
            Ok(_) => {}
            Err(error) => {
                tracing::warn!("Could not wait for the session's subagents: {error:#}");
                return;
            }
        }
    }
}

/// Interrupt the session's running subagents, then wait for them to settle,
/// all by `ends`. Returns the interrupts the server answered; a failed one
/// leaves its subagent running, and is logged.
pub async fn stop_subagents(
    client: &Client,
    session_id: &str,
    reason: &str,
    ends: Instant,
) -> Vec<SubagentInterruptResult> {
    let params = json!({ "sessionId": session_id, "reason": reason });
    let interrupt = client.request(method::SESSION_SUBAGENTS_INTERRUPT, params);
    let results = match tokio::time::timeout_at(ends, interrupt).await {
        Ok(Ok(value)) => match serde_json::from_value::<SubagentsInterruptResponse>(value) {
            Ok(response) => response.results,
            Err(error) => {
                tracing::warn!("Unreadable subagent interrupt: {error}");
                Vec::new()
            }
        },
        Ok(Err(error)) => {
            tracing::warn!("Could not interrupt the session's subagents: {error:#}");
            return Vec::new();
        }
        Err(_) => return Vec::new(),
    };
    for failed in results.iter().filter(|r| r.outcome == "failed") {
        let error = failed.error.as_deref().unwrap_or_default();
        tracing::warn!("Could not interrupt subagent {}: {error}", failed.name);
    }
    let timeout_ms = millis_until(ends, Duration::ZERO);
    let params = json!({ "sessionId": session_id, "timeoutMs": timeout_ms });
    let wait = client.request(method::SESSION_SUBAGENTS_WAIT, params);
    let _ = tokio::time::timeout_at(ends, wait).await;
    results
}

/// The session's subagents for the report, read by `ends` with time left to
/// stop the session. One whose store does not answer in time is listed
/// without its usage; none are when they cannot be read at all.
pub async fn read_subagents(
    client: &Client,
    session_id: &str,
    ends: Instant,
) -> Vec<SubagentReport> {
    let timeout_ms = millis_until(ends, READ_MARGIN).min(READ_TIMEOUT_CAP_MS);
    let params = json!({ "sessionId": session_id, "timeoutMs": timeout_ms });
    let read = client.request(method::SESSION_SUBAGENTS_READ, params);
    let response = match tokio::time::timeout_at(ends, read).await {
        Ok(Ok(value)) => serde_json::from_value::<SubagentsReadResponse>(value),
        Ok(Err(error)) => {
            tracing::warn!("Could not read the session's subagents: {error:#}");
            return Vec::new();
        }
        Err(_) => {
            tracing::warn!("The session's subagents were not read within the grace");
            return Vec::new();
        }
    };
    match response {
        Ok(response) => response
            .subagents
            .into_iter()
            .map(subagent_report)
            .collect(),
        Err(error) => {
            tracing::warn!("Unreadable session subagents: {error}");
            Vec::new()
        }
    }
}

/// A subagent as the export has it. Its cost is the server's, priced at its
/// own model's rates, cached input included, or the root's when its model is
/// unknown; the pricing fields say which.
pub fn subagent_report(run: SubagentRun) -> SubagentReport {
    let status =
        serde_json::from_value(Value::String(run.status)).unwrap_or(RunSubagentStatus::Unknown);
    SubagentReport {
        session_dir: run.session_dir.map(PathBuf::from),
        subagent: RunSubagent {
            session_id: run.session_id,
            parent_session_id: run.parent_session_id,
            root_session_id: run.root_session_id,
            agent_name: run.name,
            agent_type: run.agent_type,
            spawn_action_id: run.spawn_action_id,
            spawn_call_id: run.spawn_call_id,
            status,
            model: run.model,
            usage: run.usage.map(|usage| RunUsage {
                input_tokens: usage.input_tokens,
                output_tokens: usage.output_tokens,
                cached_input_tokens: usage.cached_input_tokens,
                total_tokens: usage.total_tokens,
            }),
            cost_usd: run.cost_usd,
            pricing_model: run.pricing_model,
            pricing_fallback: run.pricing_fallback,
            steps: run.steps,
            journal_dir: None,
        },
    }
}

fn millis_until(ends: Instant, margin: Duration) -> u64 {
    let left = ends
        .saturating_duration_since(Instant::now())
        .saturating_sub(margin);
    u64::try_from(left.as_millis()).unwrap_or(u64::MAX)
}
