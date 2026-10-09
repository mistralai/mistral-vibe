//! The entrypoint's setup-side driving: `--setup`'s serialized session-less
//! round (spawn, initialize, wizard, exit), plus the rounds driver that
//! folds the worktree replay and hands every `LoopExit::NeedsOnboarding`
//! to the live-verdict wizard round before re-entering the loop on the
//! round's child.

use std::sync::Arc;

use anyhow::Result;
use tokio::sync::{mpsc, oneshot};
use tokio::time::timeout;

use crate::app::App;
use crate::event_loop::{EventLoop, LoopExit, RunOutcome};
use crate::server::signal::ShutdownSignal;
use crate::server::Client;
use crate::startup::{StartupEvent, StartupRecorder};
use crate::terminal::Tui;
use crate::utils::history_persist::Persister;

use super::exit;
use super::wizard::boot::SetupBoot;
use super::wizard::OnboardingState;
use super::{onboarding_round, run_setup_round, SetupRound, SetupVerdict};

/// How long a cancelled round waits for a pending boot before SIGKILLing
/// the child (shared with the pre-loop round).
pub(crate) const BOOT_CANCEL_GRACE: std::time::Duration = std::time::Duration::from_secs(1);

/// How the rounds ended for the entrypoint.
pub enum RoundsOutcome {
    /// The loop finished; the entrypoint runs its usual exit sequence.
    Finished {
        result: anyhow::Result<()>,
        summary: Option<crate::session_exit::SessionExitSummary>,
    },
    /// Exit 0 without the loop's exit sequence: the wizard was cancelled
    /// (its flushes ran inside the round), or the replay's startup ended
    /// the run before the loop ever entered.
    ExitSuccess,
    /// Exit 1: the wizard round failed the run (the failure message and
    /// the terminal restore ran inside the round).
    ExitFailure,
}

/// `--setup`'s session-less round (Python `require_api_key_or_onboard`
/// with `force`): the wizard owns the screen from its first paint while
/// the boot (spawn, initialize, `setup/status`) overlaps behind the
/// welcome screen — the flow's welcome gate holds the theme transition
/// until the status lands, so the seed still precedes every choice. The
/// run prints and exits without a handshake, a session, or a trust gate.
#[allow(clippy::too_many_arguments)]
pub async fn setup_round(
    terminal: &mut Tui,
    app: &mut App,
    input: &mut mpsc::Receiver<crossterm::event::Event>,
    shutdown: &mut ShutdownSignal,
    launch: &crate::server::Launch,
    timings: &mut StartupRecorder,
) -> Result<std::process::ExitCode> {
    let (boot_tx, boot_rx) = oneshot::channel();
    // The child is out at spawn time: a cancelled round can kill it.
    let (child_tx, child_rx) = oneshot::channel();
    // A joined future, not a spawned task: the spawn mark records into
    // `timings`, whose borrow cannot cross a task boundary.
    let boot = async {
        let (client, child, _notifications, _crash_rx) = match Client::spawn(launch.clone()).await {
            Ok(spawned) => spawned,
            Err(error) => {
                // The Sentry bridge only carries ERROR records, so the
                // startup window's own failure sites log their own fatal
                // errors.
                tracing::error!(vibe_boundary = "startup", fatal = true, "{error:#}");
                let _ = boot_tx.send(SetupBoot::Spawn(error.context("spawn app-server")));
                return None;
            }
        };
        timings.record("child_spawned");
        let client = Arc::new(client);
        let _ = child_tx.send(child);
        // A failed initialize leaves no setup surface to fall back to, so
        // the run fails clearly instead of pretending.
        if crate::startup::initialize_connection(&client)
            .await
            .is_err()
        {
            let _ = boot_tx.send(SetupBoot::Init);
            return None;
        }
        let boot = match crate::setup::auth::rpc::status(&client, None).await {
            Ok(status) => SetupBoot::Ready {
                client: client.clone(),
                status: Box::new(status),
            },
            Err(crate::setup::auth::rpc::SetupError::Unavailable) => SetupBoot::Unavailable,
            Err(crate::setup::auth::rpc::SetupError::Failed(error)) => SetupBoot::Status(error),
        };
        let _ = boot_tx.send(boot);
        Some(client)
    };
    tokio::pin!(boot);
    let mut wizard = OnboardingState::default();
    let round = run_setup_round(
        terminal,
        app,
        input,
        shutdown,
        &mut wizard,
        super::wizard::boot::Boot::Pending(boot_rx),
    );
    tokio::pin!(round);
    let mut booted: Option<Option<Arc<Client>>> = None;
    let round = tokio::select! {
        round = &mut round => round,
        client = &mut boot => {
            booted = Some(client);
            round.await
        }
    };
    let code = match round? {
        SetupVerdict::Exit(code) => code,
        SetupVerdict::Continue { warnings } => {
            // The welcome gate holds every wizard choice behind the boot,
            // so a completed wizard implies the booted client and child.
            let client = match booted.take() {
                Some(client) => client,
                None => boot.await,
            }
            .expect("the welcome gate holds the wizard behind the boot");
            let child = child_rx.await.expect("the boot sends the child first");
            return exit::finish_setup(&client, child, warnings).await;
        }
    };
    if booted.is_none() && timeout(BOOT_CANCEL_GRACE, &mut boot).await.is_err() {
        // The wedged request unblocks on the killed child's EOF.
        let _ = child_rx.await.map(|child| child.kill_now());
        let _ = boot.await;
    }
    Ok(code)
}

/// Paint the first frame of a relaunched round (the wizard's last frame
/// owns the screen otherwise; the retried handshake's `Ready` repaints
/// only when it lands), marking the startup metric's first frame — a
/// pre-loop paint already marked it, and the mark is once.
pub fn paint_relaunch(mut run: EventLoop, timings: &mut StartupRecorder) -> EventLoop {
    let _ = crate::terminal::draw(&mut run.terminal, &mut run.app);
    timings.record("first_draw");
    crate::startup::mark_first_draw();
    run
}

/// The wizard loop guard: a second CONSECUTIVE missing-key verdict for the
/// same provider means the wizard's persisted key never satisfied the
/// server — a clear error beats reopening the wizard forever (an unset
/// custom env var with `--agent` loops without it).
#[derive(Default)]
pub struct OnboardingGuard {
    last: Option<String>,
}

impl OnboardingGuard {
    /// Whether this verdict repeats the previous one's provider, and so
    /// must fail the run instead of reopening the wizard.
    pub fn repeats(&mut self, provider: &str) -> bool {
        let repeat = self.last.as_deref() == Some(provider);
        self.last = Some(provider.to_owned());
        repeat
    }
}

/// The guard's exit: restore the terminal the loop still owns, print the
/// error naming the provider and its env var, then take the failure exit.
fn guard_exit(
    run: EventLoop,
    provider: &str,
    env_key: Option<String>,
    history_flush: &std::sync::Arc<Persister>,
    timings: &mut StartupRecorder,
) -> RoundsOutcome {
    drop(run.terminal_guard);
    crate::terminal::release(run.terminal);
    let remedy = match env_key {
        Some(key) => format!("Set the {key} environment variable, or run \"vibe --setup\"."),
        None => "Run \"vibe --setup\" to configure it.".to_owned(),
    };
    crate::session_exit::print_error(&format!(
        "Setup finished, but the API key for provider \"{provider}\" is still missing. {remedy}"
    ));
    super::exit::exit_flushes(history_flush, timings);
    RoundsOutcome::ExitFailure
}

/// Flush and fold one round exit into the entrypoint's outcome (Python
/// prints and exits inside `run_onboarding`'s caller).
fn round_exit(
    code: std::process::ExitCode,
    history_flush: &std::sync::Arc<Persister>,
    timings: &mut StartupRecorder,
) -> RoundsOutcome {
    super::exit::exit_flushes(history_flush, timings);
    match code {
        std::process::ExitCode::SUCCESS => RoundsOutcome::ExitSuccess,
        _ => RoundsOutcome::ExitFailure,
    }
}

/// Drive the constructed loop to its end (the entrypoint's half of Python's
/// `run_textual_ui` caller): the worktree replay folds first — its buffered
/// missing-key verdict raises the same exit as the live ready stream — and
/// every `NeedsOnboarding` runs the pre-session wizard round, then re-enters
/// the loop on the round's child with the stale replay dropped.
pub async fn run_rounds(
    mut run: EventLoop,
    replay: Option<Vec<StartupEvent>>,
    history_flush: &std::sync::Arc<Persister>,
    timings: &mut StartupRecorder,
) -> Result<RoundsOutcome> {
    let mut guard = OnboardingGuard::default();
    if let Some(startup) = replay {
        match run.replay_startup(startup, timings)? {
            Some(LoopExit::Quit) => return Ok(RoundsOutcome::ExitSuccess),
            Some(LoopExit::NeedsOnboarding { provider, env_key }) => {
                if guard.repeats(&provider) {
                    return Ok(guard_exit(run, &provider, env_key, history_flush, timings));
                }
                match onboarding_round(run, Some(provider)).await? {
                    SetupRound::Loop(relaunch) => run = paint_relaunch(*relaunch, timings),
                    SetupRound::Exit(code) => return Ok(round_exit(code, history_flush, timings)),
                }
            }
            None => {}
        }
    }
    loop {
        match run.run().await {
            RunOutcome::Finished { result, summary } => {
                return Ok(RoundsOutcome::Finished { result, summary })
            }
            RunOutcome::NeedsOnboarding {
                parts,
                provider,
                env_key,
            } => {
                if guard.repeats(&provider) {
                    return Ok(guard_exit(
                        *parts,
                        &provider,
                        env_key,
                        history_flush,
                        timings,
                    ));
                }
                match onboarding_round(*parts, Some(provider)).await? {
                    SetupRound::Loop(relaunch) => run = paint_relaunch(*relaunch, timings),
                    SetupRound::Exit(code) => return Ok(round_exit(code, history_flush, timings)),
                }
            }
        }
    }
}
