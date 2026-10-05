//! The probe-negative normal launch's pre-loop wizard round (Python
//! `require_api_key_or_onboarding`: a keyless run's wizard paints
//! instantly — Python's needs no server): the wizard opens UNSEEDED,
//! the boot (spawn, initialize, `setup/status`) overlaps behind the
//! welcome screen, and the flow's welcome gate holds every choice until
//! the seed lands. The boot's answer ends the round either way: a missing
//! key runs the wizard to completion, and a keyed-after-all answer (or a
//! failed status) closes the welcome box and lets the handshake speak.

use std::sync::Arc;

use anyhow::Result;
use tokio::sync::{mpsc, oneshot};
use tokio::time::timeout;

use crate::app::App;
use crate::server::signal::ShutdownSignal;
use crate::server::Client;
use crate::setup::auth::rpc;
use crate::startup::StartupRecorder;
use crate::terminal::Tui;

use super::exit;
use super::rounds::BOOT_CANCEL_GRACE;
use super::wizard::boot::SetupBoot;
use super::wizard::OnboardingState;
use super::{run_setup_round, SetupVerdict};

/// The round's booted child: every path that leaves the round hands the
/// same initialized child to the session handshake.
pub struct PreloopBoot {
    pub client: Arc<Client>,
    pub child: crate::server::child::ChildHandle,
    pub notifications: mpsc::Receiver<crate::server::Notification>,
    pub crash_rx: tokio::sync::watch::Receiver<bool>,
    /// The `initialize` version the retried handshake skips re-running.
    pub server_version: Option<String>,
    /// The wizard's continue-anyway warnings, owed on the real terminal
    /// before the loop repaints (Python prints them in every mode).
    pub warning: Option<String>,
}

/// How the pre-loop round ended for the entrypoint.
pub enum PreloopEnd {
    /// The handshake retries on the round's child and the loop enters
    /// normally (the wizard completed on it, or closed before any choice).
    /// Boxed: the child parts dwarf the exit code (clippy's large-variant
    /// rule), matching the rounds driver's `Round`.
    Booted(Box<PreloopBoot>),
    /// The wizard was cancelled (exit 0) or failed (exit 1): the child is
    /// reaped and the messages printed inside; the entrypoint restores the
    /// terminal like the `--setup` exit.
    Exit(std::process::ExitCode),
}

/// The overlapped boot's answer to the round.
enum BootAnswer {
    Missing {
        client: Arc<Client>,
        status: Box<rpc::SetupStatus>,
        version: Option<String>,
    },
    Handshake {
        client: Arc<Client>,
        version: Option<String>,
    },
    /// `Client::spawn` failed: the round fails the run.
    Spawn(anyhow::Error),
}

/// The child's channel-owned halves, out at spawn time so a cancelled
/// round can reach the child before the wedged request unblocks.
struct ChildParts {
    child: crate::server::child::ChildHandle,
    notifications: mpsc::Receiver<crate::server::Notification>,
    crash_rx: tokio::sync::watch::Receiver<bool>,
}

/// The probe-negative pre-loop round: the wizard owns the first paint
/// while the joined boot overlaps behind the welcome screen (the spawn
/// mark records into `timings`, whose borrow cannot cross a task
/// boundary). The seed rides the same pending-boot flow `--setup` uses.
pub async fn round(
    terminal: &mut Tui,
    app: &mut App,
    input: &mut mpsc::Receiver<crossterm::event::Event>,
    shutdown: &mut ShutdownSignal,
    launch: &crate::server::Launch,
    timings: &mut StartupRecorder,
) -> Result<PreloopEnd> {
    let (seed_tx, seed_rx) = oneshot::channel::<SetupBoot>();
    let (parts_tx, parts_rx) = oneshot::channel::<ChildParts>();
    let boot = async {
        let (client, child, notifications, crash_rx) = match Client::spawn(launch.clone()).await {
            Ok(spawned) => spawned,
            Err(error) => {
                // The Sentry bridge only carries ERROR records, so the
                // startup window's own failure sites log their own fatal
                // errors.
                tracing::error!(vibe_boundary = "startup", fatal = true, "{error:#}");
                return BootAnswer::Spawn(error.context("spawn app-server"));
            }
        };
        timings.record("child_spawned");
        let client = Arc::new(client);
        let _ = parts_tx.send(ChildParts {
            child,
            notifications,
            crash_rx,
        });
        let version = crate::startup::initialize_connection(&client).await.ok();
        let status = rpc::status(&client, None).await.ok();
        // The probe-wrong decision: only a clean missing-key status keeps
        // the wizard open — welcome-box-for-~1s-then-chat replaces the
        // blank-then-chat the held loop showed that user. A keyed answer
        // (the probe was wrong, a custom provider's env var) or a failed
        // status/initialize hands the child to the handshake, whose typed
        // verdict stays the backstop.
        match status.filter(|status| version.is_some() && !status.has_api_key) {
            Some(status) => BootAnswer::Missing {
                client,
                status: Box::new(status),
                version,
            },
            None => BootAnswer::Handshake { client, version },
        }
    };
    tokio::pin!(boot);
    let mut wizard = OnboardingState::default();
    let round = run_setup_round(
        terminal,
        app,
        input,
        shutdown,
        &mut wizard,
        super::wizard::boot::Boot::Pending(seed_rx),
    );
    tokio::pin!(round);
    enum Step {
        Closed(SetupVerdict),
        Answered(BootAnswer),
    }
    let step = tokio::select! {
        biased;
        // Cancel wins the race with the boot's answer: the user who quit
        // during the welcome window exits, never lands in the chat.
        first = &mut round => Step::Closed(first?),
        answer = &mut boot => Step::Answered(answer),
    };
    match step {
        Step::Closed(verdict) => {
            // The welcome gate holds every choice until the seed lands, so
            // only Cancel can close before the boot answered; a Continue
            // here means the boot answer never armed its seed, so the run
            // fails rather than pretending the wizard completed.
            let code = match verdict {
                SetupVerdict::Exit(code) => code,
                SetupVerdict::Continue { .. } => std::process::ExitCode::from(1),
            };
            match timeout(BOOT_CANCEL_GRACE, &mut boot).await {
                Ok(BootAnswer::Missing { client, .. } | BootAnswer::Handshake { client, .. }) => {
                    let parts = parts_rx
                        .await
                        .expect("the boot sends the child parts first");
                    super::reap_round_child(&client, parts.child).await;
                }
                // A failed spawn behind the cancelled wizard still exits 0.
                Ok(BootAnswer::Spawn(_)) => {}
                Err(_) => {
                    // The wedged request unblocks on the killed child's EOF.
                    let _ = parts_rx.await.map(|parts| parts.child.kill_now());
                    let _ = boot.await;
                }
            }
            Ok(PreloopEnd::Exit(code))
        }
        Step::Answered(BootAnswer::Spawn(error)) => Err(error),
        Step::Answered(BootAnswer::Handshake { client, version }) => {
            // Only the welcome screen was ever up and the gate held every
            // choice, so dropping the wizard loses nothing.
            let parts = parts_rx
                .await
                .expect("the boot sends the child parts first");
            Ok(booted(client, version, None, parts))
        }
        Step::Answered(BootAnswer::Missing {
            client,
            status,
            version,
        }) => {
            let _ = seed_tx.send(SetupBoot::Ready {
                client: client.clone(),
                status,
            });
            match round.await? {
                SetupVerdict::Continue { warnings } => {
                    let warning = exit::warnings_message(&warnings);
                    let parts = parts_rx
                        .await
                        .expect("the boot sends the child parts first");
                    Ok(booted(client, version, warning, parts))
                }
                SetupVerdict::Exit(code) => {
                    let parts = parts_rx
                        .await
                        .expect("the boot sends the child parts first");
                    super::reap_round_child(&client, parts.child).await;
                    Ok(PreloopEnd::Exit(code))
                }
            }
        }
    }
}

/// The booted child's handoff: the completed-wizard and keyed-after-all
/// endings both hand the same child to the retried handshake — the
/// server's own `os.environ` mutation is what makes a persisted key
/// visible to the session.
fn booted(
    client: Arc<Client>,
    version: Option<String>,
    warning: Option<String>,
    parts: ChildParts,
) -> PreloopEnd {
    PreloopEnd::Booted(Box::new(PreloopBoot {
        client,
        child: parts.child,
        notifications: parts.notifications,
        crash_rx: parts.crash_rx,
        server_version: version,
        warning,
    }))
}
