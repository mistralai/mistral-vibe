//! Headless run lifecycle: stop requests, time limits, budgets, export.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tokio::sync::Notify;

use crate::run_export::{RunError, RunLimits, RunOutcome, RunReport, RunResult};

mod turn_counter;
pub use turn_counter::TurnCounter;

/// How long a run may take to wind down once it ends or is stopped.
pub const STOP_GRACE_SECONDS: f64 = 10.0;

/// The part of the grace a stopped run keeps for its report: stopping the
/// turn and the subagents may not use it, so the report can still read them.
pub const REPORT_GRACE_SECONDS: f64 = 3.0;

/// Raised when SIGTERM arrives before the run is listening for it.
#[derive(Debug)]
pub struct RunTerminated;

impl std::fmt::Display for RunTerminated {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Run terminated by SIGTERM")
    }
}

impl std::error::Error for RunTerminated {}

/// Requests to stop a headless run early: SIGTERM, the time limit, and the
/// token and price budgets.
pub struct StopRequests {
    limits: RunLimits,
    deadline: Option<Instant>,
    requested: Arc<Mutex<Option<RunOutcome>>>,
    notify: Arc<Notify>,
    abort_message: Mutex<Option<String>>,
}

impl StopRequests {
    pub fn new(limits: RunLimits) -> Self {
        let deadline = limits
            .time_limit_s
            .map(|s| Instant::now() + Duration::from_secs_f64(s));
        Self {
            limits,
            deadline,
            requested: Arc::new(Mutex::new(None)),
            notify: Arc::new(Notify::new()),
            abort_message: Mutex::new(None),
        }
    }

    pub fn limits(&self) -> &RunLimits {
        &self.limits
    }

    /// Record a stop request. The first one wins.
    pub fn request(&self, outcome: RunOutcome) {
        let mut guard = self.requested.lock().unwrap();
        if guard.is_none() {
            *guard = Some(outcome);
            drop(guard);
            // notify_one, not notify_waiters: it stores a permit, so a
            // request that lands between wait()'s check and its first poll
            // still wakes it; notify_waiters would lose that request.
            self.notify.notify_one();
        }
    }

    /// The `--agent-socket` server aborted the run; its message is the export's error.
    pub fn abort(&self, message: &str) {
        if self.requested_outcome().is_none() {
            *self.abort_message.lock().unwrap() = Some(message.to_owned());
            self.request(RunOutcome::Aborted);
        }
    }

    /// The server's abort, when it is what stopped the run.
    pub fn abort_error(&self) -> Option<RunError> {
        let message = self.abort_message.lock().unwrap().clone()?;
        (self.requested_outcome() == Some(RunOutcome::Aborted)).then_some(RunError {
            message,
            code: None,
        })
    }

    /// The outcome that was requested, if any.
    pub fn requested_outcome(&self) -> Option<RunOutcome> {
        *self.requested.lock().unwrap()
    }

    /// Wait for a stop request or the deadline. Returns the outcome that
    /// stopped the run.
    pub async fn wait(&self) -> RunOutcome {
        // If already requested, return immediately.
        if let Some(outcome) = *self.requested.lock().unwrap() {
            return outcome;
        }

        let deadline_wait = async {
            if let Some(deadline) = self.deadline {
                let now = Instant::now();
                if deadline <= now {
                    self.request(RunOutcome::Deadline);
                } else {
                    tokio::time::sleep(deadline - now).await;
                    self.request(RunOutcome::Deadline);
                }
            } else {
                std::future::pending::<()>().await;
            }
        };

        let notify_wait = self.notify.notified();

        tokio::select! {
            _ = deadline_wait => {}
            _ = notify_wait => {}
        }

        self.requested_outcome().unwrap_or(RunOutcome::Terminated)
    }

    /// Observe usage stats and stop the run if a budget is exceeded.
    pub fn observe_usage(&self, total_tokens: u64, cost: f64) {
        if let Some(max_tokens) = self.limits.max_tokens {
            if total_tokens > max_tokens {
                self.request(RunOutcome::TokenLimit);
                return;
            }
        }
        if let Some(max_price) = self.limits.max_price {
            if cost > max_price {
                self.request(RunOutcome::PriceLimit);
            }
        }
    }
}

/// One `vibe -p` invocation: ends the process with its outcome's exit code
/// after writing the export, whichever path it leaves by.
pub struct HeadlessRun {
    pub output_dir: Option<PathBuf>,
    pub stop: Arc<StopRequests>,
    pub started_at: String,
}

impl HeadlessRun {
    pub fn new(output_dir: Option<PathBuf>, stop: Arc<StopRequests>, started_at: String) -> Self {
        Self {
            output_dir,
            stop,
            started_at,
        }
    }

    /// The headless run the arguments ask for, or None for interactive mode.
    /// Built as soon as the arguments are parsed, so every later failure can
    /// leave an export and the time limit counts from process start.
    pub fn for_cli(cli: &crate::cli::Cli) -> Option<Self> {
        if !cli.is_headless() {
            return None;
        }
        let limits = RunLimits {
            max_turns: cli.max_turns,
            max_price: cli.max_price,
            max_tokens: cli.max_tokens,
            time_limit_s: cli.time_limit,
        };
        Some(Self::new(
            cli.output_dir.clone(),
            Arc::new(StopRequests::new(limits)),
            iso_now(),
        ))
    }

    /// Fail with a usage or config error: print to stderr, write export, and
    /// return the outcome's exit code.
    pub fn fail(&self, outcome: RunOutcome, message: &str) -> u8 {
        eprintln!("Error: {message}");
        self.finish(RunReport {
            result: RunResult {
                outcome,
                stop_reason: None,
                error: Some(RunError {
                    message: message.to_owned(),
                    code: None,
                }),
                session_id: None,
                usage: None,
                cost_usd: None,
                config: None,
                steps: None,
                warnings: None,
            },
            final_response: None,
            session_dir: None,
            subagents: Vec::new(),
        })
    }

    /// Finish the run: write the export if output_dir is set, and return the
    /// outcome's exit code. Exiting is the caller's: returning lets the
    /// client drop kill the app-server's process group and the caller's
    /// Sentry flush run.
    pub fn finish(&self, report: RunReport) -> u8 {
        if let Err(error) = self.export(&report) {
            eprintln!("Error: {error}");
        }
        report.result.outcome.exit_code()
    }

    /// Write the export if output_dir is set: its path, or why it could not be written.
    pub fn export(&self, report: &RunReport) -> Result<Option<PathBuf>, String> {
        let Some(dir) = self.output_dir.as_deref() else {
            return Ok(None);
        };
        self.write_export(dir, report)
            .map(Some)
            .map_err(|e| format!("could not write the run export: {e}"))
    }

    fn write_export(&self, output_dir: &Path, report: &RunReport) -> std::io::Result<PathBuf> {
        use crate::run_export::{copy_journal, export_subagents, write_run_export, RunExport};

        let journal_dir = report.session_dir.as_ref().and_then(|sd| {
            if sd.is_dir() {
                match copy_journal(sd, output_dir) {
                    Ok(name) => Some(name),
                    Err(e) => {
                        tracing::warn!("Could not copy the session journal: {e}");
                        None
                    }
                }
            } else {
                None
            }
        });

        let export = RunExport {
            result: report.result.clone(),
            schema_version: 1,
            vibe_version: env!("CARGO_PKG_VERSION").to_owned(),
            journal_dir,
            started_at: self.started_at.clone(),
            ended_at: iso_now(),
            limits: self.stop.limits().clone(),
            exit_code: report.result.outcome.exit_code(),
            subagents: export_subagents(output_dir, &report.subagents),
        };
        write_run_export(output_dir, &export)
    }
}

/// ISO 8601 UTC timestamp without pulling in chrono. Microsecond precision,
/// as the export contract's timestamps.
pub fn iso_now() -> String {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = duration.as_secs();
    let micros = duration.subsec_micros();

    let days = (secs / 86400) as i64;
    let secs_of_day = secs % 86400;
    let hour = secs_of_day / 3600;
    let min = (secs_of_day % 3600) / 60;
    let sec = secs_of_day % 60;

    // Civil date from days since epoch (Howard Hinnant's algorithm).
    let z = days + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };

    format!("{y:04}-{m:02}-{d:02}T{hour:02}:{min:02}:{sec:02}.{micros:06}Z")
}

/// A `--time-limit` that is not a positive, finite number is a usage error,
/// not a panic in `Duration::from_secs_f64`.
pub fn invalid_time_limit(seconds: Option<f64>) -> Option<String> {
    let seconds = seconds?;
    if seconds.is_finite() && seconds > 0.0 {
        None
    } else {
        Some(format!("--time-limit must be a positive number: {seconds}"))
    }
}
