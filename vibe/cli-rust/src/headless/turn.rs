//! The turn loop: consume notifications until the turn ends, and finalize
//! the output once it does.

use std::sync::{Arc, Mutex};

use serde_json::Value;
use tokio::sync::mpsc;

use super::drive::RunState;
use super::pre_turn;
use super::subagents::wait_for_subagents;
use super::usage::UsageBaseline;
use crate::cli::OutputFormat;
use crate::headless_history::read_history;
use crate::headless_output::Output;
use crate::headless_run::{StopRequests, TurnCounter};
use crate::run_export::{turn_stop_from_turn, RunOutcome, TurnStop};
use crate::server::{notification, Client, Notification, PublicTurn};

/// How the turn ended, as far as the turn loop can tell.
pub(super) enum TurnEnd {
    /// The turn completed; carries its stop reason and the final response.
    Completed {
        stop_reason: Option<TurnStop>,
        final_response: Option<String>,
    },
    /// The turn failed; carries the wire error's code and message.
    Failed {
        code: Option<String>,
        message: String,
    },
}

/// Consume notifications until the turn ends. The app-server ignores
/// maxTurns, so the client counts assistant turns and stops the run as a
/// limit when one more would exceed it; usage stats feed the budgets.
#[allow(clippy::too_many_arguments)]
pub(super) async fn run_turn_loop(
    client: &Arc<Client>,
    notifications: &mut mpsc::Receiver<Notification>,
    output: &mut Output,
    session_id: &str,
    settled: Vec<Notification>,
    stop: &Arc<StopRequests>,
    turn_id: &str,
    usage_baseline: &UsageBaseline,
    state: &Mutex<RunState>,
) -> TurnEnd {
    let mut turns = TurnCounter::new(stop.limits().max_turns);
    let mut settled = settled.into_iter();
    loop {
        let notification = match settled.next() {
            Some(notification) => notification,
            None => match notifications.recv().await {
                Some(notification) => notification,
                None => {
                    return TurnEnd::Failed {
                        code: None,
                        message: "app-server closed".to_owned(),
                    };
                }
            },
        };
        match notification.method.as_str() {
            notification::HISTORY_ENTRY_ADDED | notification::HISTORY_ENTRY_UPDATED => {
                if notification.method == notification::HISTORY_ENTRY_ADDED {
                    if let Some(entry) = notification.params.get("entry") {
                        if turns.observe(entry) {
                            // One assistant turn too many: stop as a limit.
                            // The outer race picks the stop up and interrupts.
                            stop.request(RunOutcome::TurnLimit);
                        }
                    }
                }
                match output.consume_entry(&notification.params) {
                    Ok(Some(entry)) => {
                        if let Err(error) = output.emit_entry(&entry) {
                            return TurnEnd::Failed {
                                code: None,
                                message: format!("write streaming entry: {error}"),
                            };
                        }
                    }
                    Ok(None) => {}
                    Err(error) => {
                        return TurnEnd::Failed {
                            code: None,
                            message: format!("{error:#}"),
                        };
                    }
                }
            }
            notification::AGENT_SOCKET_ABORTED => {
                // The outer race picks the stop up, like a SIGTERM.
                let message = notification.params.get("message").and_then(Value::as_str);
                stop.abort(message.unwrap_or_default());
            }
            // A load that ends after the run started: the export still lists it.
            notification::WARNING if pre_turn::is_integrations_unavailable(&notification) => {
                let unavailable =
                    pre_turn::integrations_unavailable(&output.format(), &[notification]);
                state.lock().unwrap().warnings.extend(unavailable);
            }
            notification::SESSION_STATS_UPDATED => {
                // Observe usage for budget enforcement.
                if let Some(stats) = notification.params.get("stats") {
                    let stats = usage_baseline.run_stats(&crate::utils::json::read(stats));
                    stop.observe_usage(stats.total_tokens(), stats.cost());
                }
            }
            notification::TURN_COMPLETED if matches_turn(&notification.params, turn_id) => {
                // The run ends once its subagents do, so the export sees how each ended.
                wait_for_subagents(client, notifications, session_id, stop).await;
                let turn = PublicTurn::read(&notification.params);
                if turn.status() == Some("failed") {
                    return TurnEnd::Failed {
                        code: turn.error_code().map(str::to_owned),
                        message: turn.error_message().unwrap_or("Turn failed").to_owned(),
                    };
                }
                let stop_reason = turn_stop_from_turn(&turn);
                let final_response =
                    match finalize_output(client, session_id, Some(turn_id), output).await {
                        Ok(response) => response,
                        // The run cannot deliver its result, so it is an
                        // infrastructure failure.
                        Err(message) => {
                            return TurnEnd::Failed {
                                code: None,
                                message,
                            };
                        }
                    };
                return TurnEnd::Completed {
                    stop_reason,
                    final_response,
                };
            }
            notification::SERVER_DISCONNECTED => {
                return TurnEnd::Failed {
                    code: None,
                    message: "app-server closed".to_owned(),
                };
            }
            notification::CALLBACK_RESULT_FAILED => {
                return TurnEnd::Failed {
                    code: None,
                    message: notification.params.to_string(),
                };
            }
            _ => {}
        }
    }
}

/// Finalize output after a turn ends: write the history for JSON format, or
/// the turn's last assistant text for TEXT format. A write failure fails
/// the run: reporting `Finished` with no result delivered would score a run
/// that produced nothing.
pub(super) async fn finalize_output(
    client: &Arc<Client>,
    session_id: &str,
    turn_id: Option<&str>,
    output: &Output,
) -> Result<Option<String>, String> {
    let format = output.format();
    // Streaming delivered its result as entries arrived, so it never reads
    // the history: a `history/list` failure would fail a run that already
    // delivered its result.
    if matches!(format, OutputFormat::Streaming) {
        return Ok(None);
    }
    let entries = read_history(client, session_id)
        .await
        .map_err(|error| format!("read session history: {error:#}"))?;
    output
        .finalize(entries, turn_id)
        .map_err(|error| format!("write final output: {error}"))
}

fn matches_turn(params: &Value, turn_id: &str) -> bool {
    params.pointer("/turn/id").and_then(Value::as_str) == Some(turn_id)
}
