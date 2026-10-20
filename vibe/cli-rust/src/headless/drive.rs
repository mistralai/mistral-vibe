//! Driving one headless run to its report: one work task raced against one
//! stop request, one grace period to wind down.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::sync::{mpsc, Mutex as AsyncMutex};
use tokio::time::Instant;
use tracing::Instrument;

use super::report::build_report;
use super::session::run_session;
use super::subagents::stop_subagents;
use super::turn::{finalize_output, TurnEnd};
use super::usage::UsageBaseline;
use crate::cli::{HeadlessOptions, OutputFormat};
use crate::headless_output::Output;
use crate::headless_run::{StopRequests, REPORT_GRACE_SECONDS, STOP_GRACE_SECONDS};
use crate::run_export::{RunError, RunOutcome, RunReport, RunWarning, TurnStop};
use crate::server::{method, Client, Connect, Connection, Notification};

/// How the run's work ended: the turn's end, or a failure before the turn.
pub(super) enum WorkEnd {
    Turn(TurnEnd),
    Failed(RunOutcome, String),
}

/// The state the work task shares with the stop path: the stop interrupts
/// whatever the work has reached.
#[derive(Default)]
pub(super) struct RunState {
    pub(super) client: Option<Arc<Client>>,
    // Held so the work task's end does not end the app-server's side of the
    // run before the report is read and the session is stopped.
    pub(super) connection: Option<Connection>,
    // Held too: an unreceived notification stops the client, and the report's requests with it.
    pub(super) notifications: Option<Arc<AsyncMutex<mpsc::Receiver<Notification>>>>,
    pub(super) session_id: Option<String>,
    pub(super) turn_id: Option<String>,
    pub(super) config: Option<Value>,
    pub(super) usage_baseline: UsageBaseline,
    pub(super) warnings: Vec<RunWarning>,
}

/// Drive one headless run to its report: one work task raced against one
/// stop request, one grace period to wind down, and the report returned
/// rather than the process exited.
pub async fn drive(
    options: &HeadlessOptions,
    cwd: Option<String>,
    connect: Connect,
    stop: &Arc<StopRequests>,
) -> RunReport {
    let state = Arc::new(Mutex::new(RunState::default()));
    let mut work = tokio::spawn(
        run_session(options.clone(), cwd, connect, stop.clone(), state.clone()).in_current_span(),
    );

    // Race the work against the first stop request.
    enum Race {
        Work(Result<WorkEnd, tokio::task::JoinError>),
        Stopped(RunOutcome),
    }
    let race = tokio::select! {
        end = &mut work => Race::Work(end),
        outcome = stop.wait() => Race::Stopped(outcome),
    };
    // A run that ended wins over a late stop.
    let race = match race {
        Race::Stopped(_) if work.is_finished() => Race::Work((&mut work).await),
        race => race,
    };

    // One grace period covers winding down, so a run that stops exits about
    // STOP_GRACE_SECONDS later at worst.
    let grace_ends = Instant::now() + Duration::from_secs_f64(STOP_GRACE_SECONDS);

    let (outcome, stop_reason, error, final_response) = match race {
        Race::Work(end) => match end {
            Ok(WorkEnd::Turn(TurnEnd::Completed {
                stop_reason,
                final_response,
            })) => (turn_outcome(stop_reason), stop_reason, None, final_response),
            Ok(WorkEnd::Turn(TurnEnd::Failed { code, message })) => {
                let outcome = turn_error_outcome(code.as_deref());
                (outcome, None, Some(RunError { message, code }), None)
            }
            Ok(WorkEnd::Failed(outcome, message)) => (
                outcome,
                None,
                Some(RunError {
                    message,
                    code: None,
                }),
                None,
            ),
            Err(join) => (
                RunOutcome::InfrastructureFailure,
                None,
                Some(RunError {
                    message: join.to_string(),
                    code: None,
                }),
                None,
            ),
        },
        // Stopped before the session opened: nothing to wind down, and the
        // connection's drop ends whatever the work started.
        Race::Stopped(outcome) if state.lock().unwrap().session_id.is_none() => {
            work.abort();
            let _ = (&mut work).await;
            stopped_report(outcome, false, None)
        }
        Race::Stopped(outcome) => {
            // Stop the turn and the subagents, keeping REPORT_GRACE_SECONDS for the report.
            let stop_ends = grace_ends - Duration::from_secs_f64(REPORT_GRACE_SECONDS);
            stop_turn(&state, stop_ends).await;
            // Before the work, which may wait on them, and after, for any it spawned meanwhile.
            let reason = format!("the run stopped: {outcome}");
            stop_run_subagents(&state, &reason, stop_ends).await;
            let settled = tokio::time::timeout_at(stop_ends, &mut work)
                .await
                .ok()
                .and_then(|end| end.ok());
            if settled.is_none() {
                work.abort();
            }
            stop_run_subagents(&state, &reason, stop_ends).await;
            // The work finalizes its own output when it ends on its own, so a
            // stopped run finalizes once: here only when the work did not,
            // so `--output json` writes the history exactly once.
            let final_response = match settled {
                Some(WorkEnd::Turn(TurnEnd::Completed { final_response, .. })) => final_response,
                _ => finalize_stopped(&state, &options.output, grace_ends).await,
            };
            let turn_started = state.lock().unwrap().turn_id.is_some();
            stopped_report(outcome, turn_started, final_response)
        }
    };

    let report = build_report(
        &state,
        stop,
        outcome,
        stop_reason,
        error,
        final_response,
        grace_ends,
    )
    .await;
    close_app_server(&state).await;
    report
}

/// End the run's own app-server as the TUI and the run server end theirs:
/// close its stdin so it shuts down on its own, wait for it within the
/// shutdown grace, then kill its tree. The process then exits after its
/// app-server, not before it, and has reaped it. A shared app-server's
/// channel just closes, as its guard drops.
async fn close_app_server(state: &Arc<Mutex<RunState>>) {
    let (client, connection) = {
        let mut state = state.lock().unwrap();
        (state.client.clone(), state.connection.take())
    };
    if let (Some(client), Some(Connection::Child(child))) = (client, connection) {
        client.close_stdin().await;
        child.wait_with_grace().await;
    }
}

/// The outcome of a turn that ended on its own: an interrupted turn means
/// something outside stopped it.
fn turn_outcome(stop_reason: Option<TurnStop>) -> RunOutcome {
    match stop_reason {
        None => RunOutcome::Finished,
        Some(TurnStop::Limit) => RunOutcome::TurnLimit,
        Some(TurnStop::Length) => RunOutcome::Length,
        Some(TurnStop::Interrupted) => RunOutcome::Terminated,
    }
}

/// Map a turn error's wire code to the run's outcome. Codes come over the
/// wire in lower case, as the app server's `TurnErrorCode` StrEnum values.
pub fn turn_error_outcome(code: Option<&str>) -> RunOutcome {
    match code {
        Some("invalid_api_key") | Some("invalid_model") => RunOutcome::ConfigError,
        Some("context_too_long") | Some("response_too_long") => RunOutcome::Length,
        // The model's own answer, so the run is scored rather than dropped.
        Some("refusal") => RunOutcome::Refusal,
        _ => RunOutcome::InfrastructureFailure,
    }
}

/// The report fields for a run a stop request ended: the requested outcome
/// wins over however the turn ended, and a turn that was in flight gets the
/// stop reason the stop gave it.
pub fn stopped_report(
    outcome: RunOutcome,
    turn_started: bool,
    final_response: Option<String>,
) -> (
    RunOutcome,
    Option<TurnStop>,
    Option<RunError>,
    Option<String>,
) {
    let stop_reason = if turn_started {
        Some(match outcome {
            RunOutcome::TurnLimit => TurnStop::Limit,
            _ => TurnStop::Interrupted,
        })
    } else {
        None
    };
    (outcome, stop_reason, None, final_response)
}

/// Interrupt the run's turn within the grace: the interrupt carries the
/// turn's real id, since the server rejects an empty one, and a turn that
/// already ended is left alone.
async fn stop_turn(state: &Arc<Mutex<RunState>>, grace_ends: Instant) {
    let (client, session_id, turn_id) = {
        let state = state.lock().unwrap();
        (
            state.client.clone(),
            state.session_id.clone(),
            state.turn_id.clone(),
        )
    };
    let (Some(client), Some(session_id), Some(turn_id)) = (client, session_id, turn_id) else {
        return;
    };
    let _ = tokio::time::timeout_at(
        grace_ends,
        client.request(
            method::TURN_INTERRUPT,
            json!({ "sessionId": session_id, "expectedTurnId": turn_id }),
        ),
    )
    .await;
}

/// Interrupt the session's running subagents and let them settle, by `ends`.
async fn stop_run_subagents(state: &Arc<Mutex<RunState>>, reason: &str, ends: Instant) {
    let (client, session_id) = {
        let state = state.lock().unwrap();
        (state.client.clone(), state.session_id.clone())
    };
    if let (Some(client), Some(session_id)) = (client, session_id) {
        stop_subagents(&client, &session_id, reason, ends).await;
    }
}

/// The final response of a stopped run: whatever the history holds. The
/// grace bounds it too: the history read is a request, and an unresponsive
/// server must not hang a stopped run past STOP_GRACE_SECONDS.
async fn finalize_stopped(
    state: &Arc<Mutex<RunState>>,
    format: &OutputFormat,
    grace_ends: Instant,
) -> Option<String> {
    if grace_ends <= Instant::now() {
        return None;
    }
    let (client, session_id, turn_id) = {
        let state = state.lock().unwrap();
        (
            state.client.clone(),
            state.session_id.clone(),
            state.turn_id.clone(),
        )
    };
    let (client, session_id) = (client?, session_id?);
    let output = Output::new(format.clone());
    let finalized = finalize_output(&client, &session_id, turn_id.as_deref(), &output);
    match tokio::time::timeout_at(grace_ends, finalized).await {
        Ok(Ok(response)) => response,
        // The stop's outcome stands; the output just could not be delivered.
        Ok(Err(message)) => {
            tracing::warn!("Stopped run's final output failed: {message}");
            None
        }
        Err(_) => {
            tracing::warn!("Stopped run's final output read did not finish within the grace");
            None
        }
    }
}
