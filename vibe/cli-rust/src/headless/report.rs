//! Assembling the run's report for the export: runtime stats, cost, and the
//! session's stop.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tokio::time::Instant;

use super::drive::RunState;
use crate::headless_run::StopRequests;
use crate::run_export::{
    RunError, RunOutcome, RunReport, RunResult, RunUsage, RunWarning, TurnStop,
};
use crate::server::{method, RuntimeReadParams};

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
    let (client, session_id, config, usage_baseline) = {
        let state = state.lock().unwrap();
        (
            state.client.clone(),
            state.session_id.clone(),
            state.config.clone(),
            state.usage_baseline.clone(),
        )
    };

    let mut usage = None;
    let mut cost_usd = None;
    let mut session_dir = None;
    let mut steps = None;
    let mut warnings = None;
    let mut rt_config = None;
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
            let stats = rt.pointer("/runtime/stats").cloned().unwrap_or(Value::Null);
            let steps_stats = stats.clone();
            let stats = usage_baseline.run_stats(&stats);
            let run_usage = RunUsage {
                input_tokens: stats
                    .get("sessionPromptTokens")
                    .and_then(Value::as_u64)
                    .unwrap_or(0),
                output_tokens: stats
                    .get("sessionCompletionTokens")
                    .and_then(Value::as_u64)
                    .unwrap_or(0),
                cached_input_tokens: stats
                    .get("sessionCachedTokens")
                    .and_then(Value::as_u64)
                    .unwrap_or(0),
                total_tokens: stats
                    .get("sessionPromptTokens")
                    .and_then(Value::as_u64)
                    .unwrap_or(0)
                    + stats
                        .get("sessionCompletionTokens")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
            };
            let cost = compute_session_cost(&stats);
            session_dir = rt
                .pointer("/sessionLog/path")
                .and_then(Value::as_str)
                .map(PathBuf::from);
            steps = steps_stats.get("modelCalls").and_then(Value::as_u64);
            warnings = runtime_warnings(rt.pointer("/runtime/issues"));
            rt_config = rt.get("config").cloned();
            usage = Some(run_usage.clone());
            cost_usd = Some(cost);
            // A spent budget wins over a turn that ended on its own (see
            // `final_outcome`); an error still wins, as it says more.
            stop.observe_usage(run_usage.total_tokens, cost);
        }

        // Stop the session (persistence boundary), within the grace.
        let _ = tokio::time::timeout_at(
            grace_ends,
            client.request(method::SESSION_STOP, json!({ "sessionId": session_id })),
        )
        .await;
    }

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
fn runtime_warnings(issues: Option<&Value>) -> Option<Vec<RunWarning>> {
    let issues = issues?.as_array()?;
    let warnings: Vec<RunWarning> = issues
        .iter()
        .filter_map(|issue| {
            Some(RunWarning {
                message: issue.get("message")?.as_str()?.to_owned(),
                source: issue.get("file").and_then(Value::as_str).map(str::to_owned),
            })
        })
        .collect();
    (!warnings.is_empty()).then_some(warnings)
}

/// Compute session cost from stats with the app server's `session_token_cost` formula.
pub(super) fn compute_session_cost(stats: &Value) -> f64 {
    let prompt_tokens = stats
        .get("sessionPromptTokens")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let completion_tokens = stats
        .get("sessionCompletionTokens")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let cached_tokens = stats
        .get("sessionCachedTokens")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let input_price = stats
        .get("inputPricePerMillion")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let output_price = stats
        .get("outputPricePerMillion")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let cached_price = stats
        .get("cachedInputPricePerMillion")
        .and_then(Value::as_f64)
        .unwrap_or(input_price);

    let cached = cached_tokens.min(prompt_tokens);
    let input_cost = (prompt_tokens - cached) as f64 * input_price + cached as f64 * cached_price;
    let output_cost = completion_tokens as f64 * output_price;
    (input_cost + output_cost) / 1_000_000.0
}
