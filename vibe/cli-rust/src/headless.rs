//! Headless (programmatic) mode: send one prompt, print the response, exit.
//!
//! The lifecycle is split into focused modules: `drive` races the work
//! against the stop requests, `session` opens the session, `turn` runs the
//! turn loop, `subagents` waits for, stops and reads the subagents, `report`
//! assembles the export, `config` builds the agent config. This module keeps
//! the public entry point.

mod config;
mod drive;
mod open;
mod pre_turn;
mod report;
mod session;
mod subagents;
mod turn;
mod usage;

use crate::cli::HeadlessOptions;
use crate::headless_run::HeadlessRun;
use crate::headless_startup::print_keyring_preflight;
use crate::run_export::RunOutcome;
use crate::server::{signal, Connect, Launch};

pub use config::{agent_config, build_disabled_tools, start_params, turn_id_from};
pub use drive::{drive, stopped_report, turn_error_outcome};
pub use open::open_failure;
pub use report::final_outcome;
pub use subagents::{read_subagents, stop_subagents, subagent_report, wait_for_subagents};
pub use usage::UsageBaseline;

/// Entry point: spawn app-server, send one turn, print output, write the
/// export, and return the outcome's exit code. Never exits the process
/// itself: returning lets the client drop kill the app-server's process
/// group and the caller's Sentry flush run. The run state comes from the
/// caller, built as soon as the arguments were parsed, so an early failure
/// can already leave an export.
pub async fn run(
    options: HeadlessOptions,
    cwd: Option<String>,
    launch: Launch,
    headless: HeadlessRun,
) -> std::process::ExitCode {
    let prompt = options.prompt.clone();
    tracing::info!("USER: {prompt}");

    let stop = headless.stop.clone();
    // Every stop signal feeds StopRequests, so a SIGTERM, SIGINT or SIGHUP
    // takes the one stop path, with its export.
    let mut shutdown = match signal::install() {
        Ok(s) => s,
        Err(e) => {
            return headless
                .fail(
                    RunOutcome::InfrastructureFailure,
                    &format!("install signal handlers: {e:#}"),
                )
                .into();
        }
    };
    {
        let stop = stop.clone();
        tokio::spawn(async move {
            loop {
                shutdown.wait().await;
                stop.request(RunOutcome::Terminated);
            }
        });
    }

    print_keyring_preflight();

    let report = drive::drive(&options, cwd, Connect::Spawn(launch), &stop).await;

    // The final output is already written; say why the run did not finish.
    if let Some(ref error) = report.result.error {
        eprintln!("Error: {}", error.message);
    } else if report.result.outcome != RunOutcome::Finished {
        eprintln!("Stopped: {}", report.result.outcome);
    }
    headless.finish(report).into()
}
