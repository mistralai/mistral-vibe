//! One run: driven on its app-server channel, exported, and announced ended.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::watch;
use tokio::time::Instant;

use super::protocol::{invalid_params, Failure, RunEnded, RunStartParams};
use crate::cli::{HeadlessOptions, HeadlessSession, OutputFormat};
use crate::headless::drive;
use crate::headless_run::{iso_now, HeadlessRun, StopRequests};
use crate::run_export::{RunError, RunLimits, RunOutcome, RunReport, RunResult};
use crate::server::{ChannelCloser, Connect, Connected};

/// How long a stopped run may take before it is cancelled.
pub const HARD_STOP: Duration = Duration::from_secs(15);
/// How long a stopped run's export may take, past its hard stop.
pub const EXPORT_GRACE: Duration = Duration::from_secs(5);

/// A run as `run/start` asks for it, validated.
pub struct RunSpec {
    pub run_id: String,
    pub options: HeadlessOptions,
    pub limits: RunLimits,
    pub config: Option<serde_json::Map<String, serde_json::Value>>,
    pub credentials: std::collections::BTreeMap<String, String>,
}

impl RunSpec {
    /// The run `params` ask for, with paths resolved against `invocation_cwd`.
    pub fn from_params(params: RunStartParams, invocation_cwd: &Path) -> Result<Self, Failure> {
        let mut issues = Vec::new();
        let mut required = |field: &str, value: &Option<String>| {
            if value.as_deref().is_none_or(str::is_empty) {
                issues.push((field.to_owned(), "required, and not empty".to_owned()));
            }
        };
        required("runId", &params.run_id);
        required("prompt", &params.prompt);
        required("agentSocket", &params.agent_socket);
        let positive = |value: Option<f64>| value.is_none_or(|v| v.is_finite() && v > 0.0);
        for (field, ok) in [
            ("maxTurns", params.max_turns != Some(0)),
            ("maxTokens", params.max_tokens != Some(0)),
            ("maxPrice", positive(params.max_price)),
            ("timeLimit", positive(params.time_limit)),
        ] {
            if !ok {
                issues.push((field.to_owned(), "must be greater than 0".to_owned()));
            }
        }
        if !issues.is_empty() {
            return Err(invalid_params(issues));
        }
        let resolve =
            |path: &str| invocation_cwd.join(crate::utils::paths::expand_tilde(Path::new(path)));
        let options = HeadlessOptions {
            prompt: params.prompt.unwrap_or_default(),
            session: params
                .resume_session_id
                .map_or(HeadlessSession::New, HeadlessSession::Resume),
            output: OutputFormat::Silent,
            max_turns: params.max_turns,
            max_price: params.max_price,
            max_tokens: params.max_tokens,
            time_limit: params.time_limit,
            output_dir: params.output_dir.as_deref().map(resolve),
            system_prompt: None,
            model: None,
            agent: params.agent,
            auto_approve: params.auto_approve,
            enabled_tools: params.enabled_tools.unwrap_or_default(),
            disabled_tools: params.disabled_tools.unwrap_or_default(),
            trust: false,
            add_dir: Vec::new(),
            worktree: None,
            agent_socket: params.agent_socket.map(PathBuf::from),
        };
        let limits = RunLimits {
            max_turns: params.max_turns,
            max_price: params.max_price,
            max_tokens: params.max_tokens,
            time_limit_s: params.time_limit,
        };
        Ok(Self {
            run_id: params.run_id.unwrap_or_default(),
            options,
            limits,
            config: params.config,
            credentials: params.credentials,
        })
    }
}

/// A started run, as the server holds it until its `run/ended`.
pub struct LiveRun {
    stop: Arc<StopRequests>,
    hard_stop: watch::Sender<Option<Instant>>,
}

impl LiveRun {
    /// Stop the run as `terminated`; it is cancelled `HARD_STOP` later.
    pub fn stop(&self) {
        self.stop.request(RunOutcome::Terminated);
        self.hard_stop.send_if_modified(|at| {
            let first = at.is_none();
            let hard_stop = crate::utils::timeout_or_env("VIBE_TEST_HARD_STOP_MS", HARD_STOP);
            at.get_or_insert_with(|| Instant::now() + hard_stop);
            first
        });
    }
}

/// Start the run's stop requests: its time limit counts from here.
pub fn start(spec: &RunSpec) -> (LiveRun, Arc<StopRequests>, watch::Receiver<Option<Instant>>) {
    let stop = Arc::new(StopRequests::new(spec.limits.clone()));
    let (hard_stop, hard_stop_rx) = watch::channel(None);
    let live = LiveRun {
        stop: stop.clone(),
        hard_stop,
    };
    (live, stop, hard_stop_rx)
}

/// Drive the run on `channel`, write its export, and say how it ended.
pub async fn serve(
    spec: RunSpec,
    channel: Result<(Connected, ChannelCloser), String>,
    cwd: Option<String>,
    stop: Arc<StopRequests>,
    mut hard_stop: watch::Receiver<Option<Instant>>,
) -> RunEnded {
    let headless = HeadlessRun::new(spec.options.output_dir.clone(), stop.clone(), iso_now());
    let mut report = match channel {
        Err(message) => failed(RunOutcome::InfrastructureFailure, message, None),
        Ok((connected, closer)) => {
            let connect = Connect::Channel(Box::new(connected));
            let run = drive(&spec.options, cwd, connect, &stop);
            tokio::select! {
                report = run => report,
                () = hard_stopped(&mut hard_stop) => {
                    closer.close();
                    tracing::warn!("The run did not wind down in time after its stop; cancelled it");
                    let message = "the run did not wind down in time after its stop".to_owned();
                    failed(RunOutcome::Terminated, message, Some("stop_timed_out"))
                }
            }
        }
    };
    redact(&mut report.result, &spec.credentials);
    let (export_path, export_error, export_error_code) = export(headless, &report, hard_stop).await;
    let result = report.result;
    tracing::info!(outcome = %result.outcome, "Run ended");
    RunEnded {
        run_id: spec.run_id,
        outcome: result.outcome,
        exit_code: result.outcome.exit_code(),
        session_id: result.session_id,
        error: result.error,
        export_path: export_path.map(|path| path.display().to_string()),
        export_error,
        export_error_code: export_error_code.map(str::to_owned),
        warnings: result.warnings,
    }
}

/// Resolves at the run's hard stop, once one is set.
async fn hard_stopped(hard_stop: &mut watch::Receiver<Option<Instant>>) {
    loop {
        let at = *hard_stop.borrow_and_update();
        match at {
            Some(at) => return tokio::time::sleep_until(at).await,
            None if hard_stop.changed().await.is_err() => std::future::pending().await,
            None => {}
        }
    }
}

/// Write the export off the runtime; once stopped, until `EXPORT_GRACE` past the hard stop.
async fn export(
    headless: HeadlessRun,
    report: &RunReport,
    mut hard_stop: watch::Receiver<Option<Instant>>,
) -> (Option<PathBuf>, Option<String>, Option<&'static str>) {
    let report = report.clone();
    let mut writing = tokio::task::spawn_blocking(move || headless.export(&report));
    let mut watching = true;
    loop {
        let deadline =
            (*hard_stop.borrow_and_update()).map(|at| at.max(Instant::now()) + EXPORT_GRACE);
        let bounded = async {
            match deadline {
                Some(deadline) => tokio::time::sleep_until(deadline).await,
                None => std::future::pending().await,
            }
        };
        tokio::select! {
            written = &mut writing => {
                return match written {
                    Ok(Ok(path)) => (path, None, None),
                    Ok(Err(error)) => (None, Some(error), None),
                    Err(join) => (None, Some(format!("could not write the run export: {join}")), None),
                };
            }
            () = bounded => {
                let message = "the run export did not finish in time".to_owned();
                return (None, Some(message), Some("export_timed_out"));
            }
            changed = hard_stop.changed(), if watching && deadline.is_none() => {
                watching = changed.is_ok();
            }
        }
    }
}

/// The report of a run that ended without the driver's.
fn failed(outcome: RunOutcome, message: String, code: Option<&str>) -> RunReport {
    RunReport {
        result: RunResult {
            outcome,
            stop_reason: None,
            error: Some(RunError {
                message,
                code: code.map(str::to_owned),
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
    }
}

/// Mask the run's credential values in what the client and the export see.
fn redact(result: &mut RunResult, credentials: &std::collections::BTreeMap<String, String>) {
    let mut secrets: Vec<&str> = credentials
        .values()
        .map(String::as_str)
        .filter(|v| !v.is_empty())
        .collect();
    secrets.sort_by_key(|secret| std::cmp::Reverse(secret.len()));
    let mask = |text: &mut String| {
        for secret in &secrets {
            if text.contains(secret) {
                *text = text.replace(secret, "[REDACTED]");
            }
        }
    };
    if let Some(error) = result.error.as_mut() {
        mask(&mut error.message);
    }
    for warning in result.warnings.iter_mut().flatten() {
        mask(&mut warning.message);
    }
}

/// The `run/ended` of a run whose task failed before it could say.
pub fn crashed(run_id: String, message: &str) -> RunEnded {
    let outcome = RunOutcome::InfrastructureFailure;
    RunEnded {
        run_id,
        outcome,
        exit_code: outcome.exit_code(),
        session_id: None,
        error: Some(RunError {
            message: format!("the run's task failed: {message}"),
            code: None,
        }),
        export_path: None,
        export_error: None,
        export_error_code: None,
        warnings: None,
    }
}
