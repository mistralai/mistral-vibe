//! The pre-session setup surface (Python `vibe/setup`): the onboarding
//! wizard and its auth helpers, the `--setup` exit, and the loop's
//! missing-key exit handling. Driven by the entrypoint only — the steady
//! event loop and the app import nothing from here (Python's `textual_ui`
//! imports nothing from `setup` either). One child per setup round
//! (decision 7): initialized for `setup/*` and reused through
//! store-credential, submit-choices, and the session (`--setup` and the
//! probe-negative pre-loop round both boot it behind the wizard's welcome
//! screen).

pub mod auth;
pub mod boot;
pub mod exit;
pub mod preloop;
pub mod rounds;
pub mod wizard;

use std::sync::Arc;

use anyhow::{anyhow, Result};
use tokio::sync::mpsc;

use crate::app::App;
use crate::event_loop::EventLoop;
use crate::server::Client;
use crate::setup::auth::rpc;
use crate::setup::wizard::OnboardingState;
use crate::startup::HandshakeParams;
use crate::terminal::Tui;

/// One wizard round's verdict for its caller, every terminal message
/// already printed inside (Python `run_onboarding`'s exit match).
#[derive(Debug, PartialEq)]
pub enum SetupVerdict {
    /// The wizard completed; the continue-anyway warnings ride along for
    /// the caller's surface (`--setup` folds them into its exit line; the
    /// interactive rounds print them before the TUI repaints).
    Continue { warnings: Vec<String> },
    /// The run exits with this code.
    Exit(std::process::ExitCode),
}

/// The live-verdict round's answer for the rounds driver.
pub enum SetupRound {
    /// The loop re-enters on the round's child. Boxed: the loop parts
    /// dwarf the enum's other arm (clippy's large-variant rule), and only
    /// one round is ever in flight.
    Loop(Box<EventLoop>),
    /// The run exits with this code (the round's messages printed and the
    /// terminal restored inside).
    Exit(std::process::ExitCode),
}

/// One wizard round's shared half (Python `require_api_key_or_onboard` /
/// `run_onboarding`): open the wizard, run its flow to the close, and fold
/// the close into the verdict. The seed precedes every choice either way —
/// a pending boot seeds behind the flow's welcome gate, a ready boot seeds
/// before the open (`--setup` and the probe-negative pre-loop round overlap
/// the boot; the missing-key round's child is up before the wizard).
pub(crate) async fn run_setup_round(
    terminal: &mut Tui,
    app: &mut App,
    input: &mut mpsc::Receiver<crossterm::event::Event>,
    shutdown: &mut crate::server::signal::ShutdownSignal,
    wizard: &mut OnboardingState,
    boot: wizard::boot::Boot,
) -> Result<SetupVerdict> {
    wizard::open(wizard);
    let close = wizard::flow::run_wizard(terminal, app, wizard, input, shutdown, boot).await?;
    Ok(wizard::actions::onboarding_finished(close))
}

/// The live verdict's wizard round (Python runs `run_onboarding` from the
/// entrypoint, never from inside the TUI): a handshake ran and failed
/// unauthorized, so stop the child that can never see the persisted key,
/// spawn a FRESH child and initialize it BEFORE the wizard, run the wizard
/// talking `setup/*` on that connection, then re-run the handshake on the
/// SAME child — the server's own `os.environ` mutation is what makes the
/// key visible to the session. The stale replay the verdict rode in on is
/// dropped, not re-driven: the retried handshake produces its own startup
/// events. A probe-negative launch never lands here pre-wizard: its
/// pre-loop round (`preloop::round`) owns the overlapped boot.
pub async fn onboarding_round(run: EventLoop, provider: Option<String>) -> Result<SetupRound> {
    let mut run = run;
    // The old child never opened a session, so EOF exit or the grace
    // SIGKILL is all the cleanup it needs.
    run.client.close_stdin().await;
    if let Some(child) = run.child.take() {
        tokio::spawn(async move {
            match child.wait_with_grace().await {
                Some(status) => {
                    tracing::debug!(status = %status, "app-server reaped before onboarding")
                }
                None => tracing::debug!("app-server SIGKILLed before onboarding grace"),
            }
        });
    }
    let (client, child, notifications, crash_rx) = match Client::spawn(run.launch.clone()).await {
        Ok(spawned) => spawned,
        Err(error) => {
            // The Sentry bridge only carries ERROR records, so the startup
            // window's own failure sites log their own fatal errors.
            tracing::error!(
                vibe_boundary = "startup",
                fatal = true,
                "respawn app-server: {error:#}"
            );
            return Err(error.context("respawn app-server"));
        }
    };
    let client = Arc::new(client);
    let server_version = crate::startup::initialize_connection(&client).await?;
    // The seed matches the failure: the provider the handshake's verdict
    // named, resolved server-side through the runtime's own `_named_provider`
    // path — never the server's active provider, which can differ (an
    // `--agent` run's config layering).
    let status = match rpc::status(&client, provider.as_deref()).await {
        Ok(status) => status,
        Err(rpc::SetupError::Unavailable) => {
            drop(run.terminal_guard);
            crate::terminal::release(run.terminal);
            crate::setup::exit::print_setup_unavailable(
                "this app-server does not support the setup methods",
            );
            reap_round_child(&client, child).await;
            return Ok(SetupRound::Exit(std::process::ExitCode::from(1)));
        }
        Err(rpc::SetupError::Failed(error)) => {
            reap_round_child(&client, child).await;
            return Err(anyhow!("setup/status: {error}"));
        }
    };
    let mut wizard = OnboardingState::default();
    let verdict = run_setup_round(
        &mut run.terminal,
        &mut run.app,
        &mut run.input,
        &mut run.shutdown,
        &mut wizard,
        wizard::boot::Boot::Ready {
            client: client.clone(),
            status: Box::new(status),
        },
    )
    .await?;
    match verdict {
        // The messages printed inside the round; the terminal restore runs
        // here, like every round exit.
        SetupVerdict::Exit(code) => {
            drop(run.terminal_guard);
            crate::terminal::release(run.terminal);
            reap_round_child(&client, child).await;
            Ok(SetupRound::Exit(code))
        }
        // Python prints the continue-anyway warnings in every mode; the round
        // owes them on the real terminal before the loop repaints.
        SetupVerdict::Continue { warnings } => {
            if let Some(message) = crate::setup::exit::warnings_message(&warnings) {
                crate::setup::exit::print_pre_tui_warning(
                    &message,
                    &mut run.terminal,
                    &mut run.terminal_guard,
                )?;
            }
            // No respawn: the child the wizard talked to IS the session's child.
            adopt_and_retry(
                &mut run,
                client,
                child,
                notifications,
                crash_rx,
                server_version,
            );
            Ok(SetupRound::Loop(Box::new(run)))
        }
    }
}

/// The round's freshly spawned child never reaches a session on the exit
/// paths: stdin EOF, then a graceful reap — `finish_setup`'s pattern, never
/// the drop's SIGKILL.
async fn reap_round_child(client: &Arc<Client>, child: crate::server::child::ChildHandle) {
    client.close_stdin().await;
    match child.wait_with_grace().await {
        Some(status) => tracing::debug!("app-server reaped after the onboarding exit: {status}"),
        None => tracing::debug!("app-server SIGKILLed after the onboarding grace"),
    }
}

/// Move the round's pre-wizard child into the loop it now serves, and
/// re-run the startup handshake on the same initialized connection (the
/// trust gate stays answered — its decision was persisted server-side on
/// the first pass).
fn adopt_and_retry(
    run: &mut EventLoop,
    client: Arc<Client>,
    child: crate::server::child::ChildHandle,
    notifications: mpsc::Receiver<crate::server::Notification>,
    crash_rx: tokio::sync::watch::Receiver<bool>,
    server_version: String,
) {
    run.client = client;
    run.child = Some(child);
    run.crash_rx = crash_rx;
    // A worktree run's retry re-runs the settle wait inside the new
    // handshake, which needs the notification stream; the event loop reads
    // the bridge, like the pre-TUI gate does.
    let notifications = if run.app.session.agent_config.worktree.is_some() {
        let (bridge_tx, bridge_rx) = mpsc::channel(256);
        run.sources.notifications = bridge_rx;
        Some((notifications, bridge_tx))
    } else {
        run.sources.notifications = notifications;
        None
    };
    tracing::info!("app-server re-spawned after onboarding; retrying handshake");
    retry_handshake(run, notifications, Some(server_version), true);
}

/// Spawn the retried handshake on the loop's own child: the pre-loop
/// round's child is already initialized (and already plumbed), so only the
/// handshake itself is owed; `None` leaves a failed initialize to be re-run
/// and surfaced by the handshake. `trust_already_resolved` is true only on
/// the onboarding round's retry — the pre-loop round's retry still runs the
/// trust gate, which is its first.
pub fn retry_handshake(
    run: &mut EventLoop,
    notifications: Option<crate::startup::WorktreeNotifications>,
    server_version: Option<String>,
    trust_already_resolved: bool,
) {
    let (trust_tx, trust_rx) = mpsc::channel::<String>(1);
    run.app.trust.tx = Some(trust_tx);
    tokio::spawn(crate::startup::handshake(
        run.client.clone(),
        run.ready_tx.clone(),
        trust_rx,
        HandshakeParams {
            cwd: run.app.session.cwd.clone(),
            show_unready_config: run.show_unready_config,
            resume: run.app.session.startup_resume.clone(),
            agent_config: run.app.session.agent_config.clone(),
            notifications,
            pre_initialized: server_version,
            trust_already_resolved,
        },
    ));
}
