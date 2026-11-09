//! Assembling the run's report for the export: runtime stats, cost, and the
//! session's stop.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tokio::time::Instant;

use super::drive::RunState;
use super::subagents::read_subagents;
use crate::headless_run::StopRequests;
use crate::run_export::{
    RunError, RunOutcome, RunReport, RunResult, RunUsage, RunWarning, TurnStop,
};
use crate::server::{method, ConfigIssue, RuntimeReadParams, RuntimeSnapshot};

/// Build the final report, within the one grace period: read runtime stats,
/// then stop the session — session/stop closes the session server-side,
/// which would make runtime/read fail, so the order matters. A session that
/// was created is stopped on every path, so a failure during startup leaves
/// none behind.
#[allow(clippy::too_many_arguments)]
pub(super) async fn build_report(
    state: &Arc<Mutex<RunState>>,
    stop: &Arc<StopRequests>,
    outcome: RunOutcome,
    stop_reason: Option<TurnStop>,
    error: Option<RunError>,
    final_response: Option<String>,
    grace_ends: Instant,
) -> RunReport {
    let (client, session_id, config, usage_baseline, run_warnings) = {
        let state = state.lock().unwrap();
        (
            state.client.clone(),
            state.session_id.clone(),
            state.config.clone(),
            state.usage_baseline.clone(),
            state.warnings.clone(),
        )
    };

    let mut usage = None;
    let mut cost_usd = None;
    let mut session_dir = None;
    let mut steps = None;
    let mut warnings = None;
    let mut rt_config = None;
    let mut subagents = Vec::new();
    if let (Some(client), Some(session_id)) = (client, session_id.clone()) {
        // Read runtime for final stats and session log path.
        let runtime_params = RuntimeReadParams {
            session_id: session_id.clone(),
        };
        let runtime_value = serde_json::to_value(&runtime_params).unwrap_or_default();
        let runtime = tokio::time::timeout_at(
            grace_ends,
            client.request(method::RUNTIME_READ, runtime_value),
        )
        .await
        .ok()
        .and_then(|r| r.ok());

        if let Some(rt) = runtime {
            let snapshot = RuntimeSnapshot::read(&rt).unwrap_or_default();
            let stats = usage_baseline.run_stats(&snapshot.stats);
            let run_usage = RunUsage {
                input_tokens: stats.session_prompt_tokens,
                output_tokens: stats.session_completion_tokens,
                cached_input_tokens: stats.session_cached_tokens,
                total_tokens: stats.total_tokens(),
            };
            let cost = stats.cost();
            session_dir = rt
                .pointer("/sessionLog/path")
                .and_then(Value::as_str)
                .map(PathBuf::from);
            steps = stats.model_calls;
            warnings = runtime_warnings(snapshot.issues);
            rt_config = rt.get("config").cloned();
            usage = Some(run_usage.clone());
            cost_usd = Some(cost);
            // A spent budget wins over a turn that ended on its own (see
            // `final_outcome`); an error still wins, as it says more.
            stop.observe_usage(run_usage.total_tokens, cost);
        }

        // Read while the session is live: stopping it closes its subagents.
        subagents = read_subagents(&client, &session_id, grace_ends).await;

        // Stop the session (persistence boundary), within the grace.
        let _ = tokio::time::timeout_at(
            grace_ends,
            client.request(method::SESSION_STOP, json!({ "sessionId": session_id })),
        )
        .await;
    }

    // The run's own warnings come after the runtime's.
    let warnings = match (warnings, run_warnings) {
        (warnings, run) if run.is_empty() => warnings,
        (warnings, run) => Some(warnings.into_iter().flatten().chain(run).collect()),
    };

    // The runtime snapshot holds the same config view as config/effective/read.
    let config = config.or(rt_config);

    // However the run then ended, the server's abort is why.
    let (outcome, error) = match stop.abort_error() {
        Some(abort) => (RunOutcome::Aborted, Some(abort)),
        None => (outcome, error),
    };
    let outcome = final_outcome(outcome, &error, stop);

    RunReport {
        result: RunResult {
            outcome,
            stop_reason,
            error,
            session_id,
            usage,
            cost_usd,
            steps,
            config,
            warnings,
        },
        final_response,
        session_dir,
        subagents,
    }
}

/// The report's outcome: a limit the run enforced itself (turn, token or
/// price) wins over a turn that ended on its own — the run went past the
/// limit it was given, whether or not the stop came in time to cut anything
/// short. An externally requested stop (deadline, signal) loses to a
/// finished turn, and an error still wins, as it says more.
pub fn final_outcome(
    outcome: RunOutcome,
    error: &Option<RunError>,
    stop: &StopRequests,
) -> RunOutcome {
    if error.is_some() {
        return outcome;
    }
    match stop.requested_outcome() {
        Some(spent @ (RunOutcome::TurnLimit | RunOutcome::TokenLimit | RunOutcome::PriceLimit)) => {
            spent
        }
        _ => outcome,
    }
}

/// Map the runtime snapshot's config issues to export warnings. Empty issues
/// mean no warnings, so the field is left out of the export.
fn runtime_warnings(issues: Vec<ConfigIssue>) -> Option<Vec<RunWarning>> {
    let warnings: Vec<RunWarning> = issues
        .into_iter()
        .map(|issue| RunWarning {
            message: issue.message,
            // A process-wide issue, such as a run-server log level, names no file.
            source: (!issue.file.is_empty()).then_some(issue.file),
        })
        .collect();
    (!warnings.is_empty()).then_some(warnings)
}
