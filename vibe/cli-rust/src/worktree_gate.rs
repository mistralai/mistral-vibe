//! The pre-TUI worktree wait, Python `_enter_worktree` parity: the Python CLI
//! prepares the worktree before the TUI exists, so the Rust client also holds
//! the terminal back until the app-server has raised its worktree, with the
//! same two stderr lines. The prep itself stays server-side (ADR 0016); the
//! client only waits for the server's settle or failure signal.

use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use tokio::sync::mpsc;
use tokio::time::Instant;

use serde_json::Value;

use crate::cli::StartupResume;
use crate::server::signal::ShutdownSignal;
use crate::server::{AgentConfig, ChildHandle, Client, Launch, Notification, WorktreeInput};
use crate::startup::{handshake, StartupEvent};

/// The settle is normally seconds (a fetch capped at 10s plus the checkout);
/// the bound only exists so a server that never announces cannot hang the
/// CLI pre-TUI. Past it, the run continues and the footer follows the move
/// whenever it does land.
pub const SETTLE_WAIT: Duration = Duration::from_secs(120);

/// Python `_enter_worktree`'s first line, printed before the TUI exists:
/// `Preparing worktree 'NAME'...`, or `Preparing worktree...` for the bare
/// flag whose name the server picks.
pub fn print_preparing(worktree: Option<&WorktreeInput>) {
    let Some(worktree) = worktree else {
        return;
    };
    let requested = match worktree {
        // Python `f" {args.worktree!r}"`: a plain filename reprs in single
        // quotes; the validator upstream rejects anything exotic.
        WorktreeInput::Create { name, .. } => format!(" '{name}'"),
        _ => String::new(),
    };
    crate::worktree_exit::print_dim(&format!("Preparing worktree{requested}..."));
}

/// Python `_enter_worktree`'s second line: `Using worktree: <path>`, where
/// the path is the session cwd the move produced (worktree root plus the
/// subdirectory the CLI was launched from).
pub fn print_using(cwd: &str) {
    crate::worktree_exit::print_dim(&format!("Using worktree: {cwd}"));
}

/// The session cwd a settle carried: the `/cwd` and `/worktree` replaces ride
/// one `session/updated`, and the value that matters is the moved cwd.
pub fn settle_cwd(params: &Value) -> Option<String> {
    let mut cwd = None;
    let mut moved = false;
    for (path, value) in crate::worktree::replace_ops(params.get("patch")?) {
        match path {
            "/cwd" => cwd = value.as_str().map(str::to_owned),
            "/worktree" => moved = !value.is_null(),
            _ => {}
        }
    }
    if moved {
        cwd
    } else {
        None
    }
}

/// The worktree the settle announced (`/worktree` replace), as tracked info.
pub fn settle_worktree(params: &Value) -> Option<crate::worktree::WorktreeInfo> {
    for (path, value) in crate::worktree::replace_ops(params.get("patch")?) {
        if path != "/worktree" {
            continue;
        }
        let worktree: crate::server::PublicSessionWorktree =
            serde_json::from_value(value.clone()).ok()?;
        return Some(crate::worktree::WorktreeInfo {
            name: worktree.name,
            branch: worktree.branch,
            path: worktree.path,
            created: worktree.created,
        });
    }
    None
}

/// A failed session-start worktree's message, when the notification is the
/// pushed failure entry: the server emits one because a client gating its UI
/// on the worktree has no turn to carry it.
pub fn failed_message(params: &Value) -> Option<String> {
    let entry = params.get("entry")?;
    let is_worktree = entry.pointer("/detail/kind").and_then(Value::as_str) == Some("worktree");
    let failed = entry.pointer("/state/status").and_then(Value::as_str) == Some("failed");
    if !is_worktree || !failed {
        return None;
    }
    entry
        .pointer("/state/error/message")
        .and_then(Value::as_str)
        .map(str::to_owned)
}

/// Everything consumed while waiting, replayed into the app once it exists.
#[derive(Default)]
pub struct Waited {
    pub notifications: Vec<Notification>,
}

/// What a settle carried: the moved session cwd and this run's prepared
/// worktree, when the worktree settled within the bound.
#[derive(Default)]
pub struct Settled {
    pub cwd: Option<String>,
    pub worktree: Option<crate::worktree::WorktreeInfo>,
}

/// Absorb the notification stream until the requested worktree settles, fails,
/// or the bound passes. The interactive pre-TUI wait replays `Waited` into the
/// app and forwards the stream; the headless one feeds it to the turn pump.
/// `Err` carries the message Python would print as `Error: ...` with exit 1.
pub async fn absorb_until_settled(
    notifications: &mut mpsc::Receiver<Notification>,
    deadline: Instant,
) -> Result<(Settled, Waited), String> {
    let mut waited = Waited::default();
    loop {
        tokio::select! {
            maybe = notifications.recv() => match maybe {
                Some(notification) => {
                    let settled_cwd = settle_cwd(&notification.params);
                    let settled_worktree = settle_worktree(&notification.params);
                    let failed = failed_message(&notification.params);
                    waited.notifications.push(notification);
                    if let Some(message) = failed {
                        return Err(message);
                    }
                    if let Some(cwd) = settled_cwd {
                        return Ok((
                            Settled {
                                cwd: Some(cwd),
                                worktree: settled_worktree,
                            },
                            waited,
                        ));
                    }
                }
                None => return Ok((Settled::default(), waited)),
            },
            _ = tokio::time::sleep_until(deadline) => return Ok((Settled::default(), waited)),
        }
    }
}

/// What a worktree run's pre-TUI wait set up: the live connection, the bridge
/// the event loop reads notifications from, and the startup events buffered
/// until the handshake's `Ready` (which is not sent until the worktree settled
/// and the resume rebind finished).
pub struct EarlyWorktreeStart {
    pub client: Arc<Client>,
    pub child: ChildHandle,
    pub notifications: mpsc::Receiver<Notification>,
    pub crash_rx: tokio::sync::watch::Receiver<bool>,
    pub ready: mpsc::Receiver<StartupEvent>,
    pub trust_tx: mpsc::Sender<String>,
    pub startup: Vec<StartupEvent>,
}

/// The Ctrl-C-during-prep marker: `main` renders Python's top-level
/// `KeyboardInterrupt` handling (dim `Bye!`, exit 0) instead of `Error:`.
#[derive(Debug)]
pub struct PrepInterrupt;

impl std::fmt::Display for PrepInterrupt {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "interrupted while preparing the worktree")
    }
}

impl std::error::Error for PrepInterrupt {}

/// Spawn the app-server pre-TUI and hold the terminal back until the worktree
/// run's `Ready`, Python's `_enter_worktree` block: the handshake owns the
/// settle wait and the worktree-scoped resume, and its `Ready` is not sent
/// until the worktree settled (or the settle bound passed) and the resume
/// rebind finished, so the terminal starts on the finished state. A signal
/// during the wait returns `PrepInterrupt`, dropping `child`, whose Drop
/// SIGKILLs the app-server's whole process group.
pub async fn early_start(
    launch: Launch,
    cwd_text: String,
    show_unready_config: bool,
    startup_resume: StartupResume,
    agent_config: AgentConfig,
    shutdown: &mut ShutdownSignal,
) -> Result<EarlyWorktreeStart> {
    print_preparing(agent_config.worktree.as_ref());
    let (client, child, notifications, crash_rx) = match Client::spawn(launch).await {
        Ok(spawned) => spawned,
        Err(error) => {
            tracing::error!(vibe_boundary = "startup", fatal = true, "{error:#}");
            return Err(error.context("spawn app-server"));
        }
    };
    let client = Arc::new(client);
    let (ready_tx, mut ready_rx) = mpsc::channel::<StartupEvent>(1);
    // The trust gate's answer, awaited by the handshake before it opens a
    // session; a worktree run is always trusted, so the gate never opens.
    let (trust_tx, trust_rx) = mpsc::channel::<String>(1);
    // The bridge carries the notifications the event loop still reads.
    let (bridge_tx, bridge_rx) = mpsc::channel::<Notification>(256);
    tokio::spawn(handshake(
        client.clone(),
        ready_tx,
        trust_rx,
        crate::startup::HandshakeParams {
            cwd: Some(cwd_text),
            show_unready_config,
            resume: startup_resume,
            agent_config,
            notifications: Some((notifications, bridge_tx)),
            // The gate's child was spawned for this handshake: it runs the
            // `initialize` itself.
            pre_initialized: None,
            trust_already_resolved: false,
        },
    ));
    let mut startup = Vec::new();
    loop {
        // A Ctrl-C here must not orphan the app-server: racing the handlers
        // returns early, and dropping `child` kills its process group.
        let event = tokio::select! {
            event = ready_rx.recv() => event,
            _ = shutdown.wait() => {
                return Err(anyhow::Error::new(PrepInterrupt));
            }
        };
        match event {
            Some(StartupEvent::Failed(message)) => {
                // Python prints `Error: {message}` on stdout and exits 1; the
                // caller's Err path does exactly that, dropping the child.
                return Err(anyhow::Error::msg(message));
            }
            Some(StartupEvent::Ready(ready)) => {
                if let Some(cwd) = &ready.settled {
                    print_using(cwd);
                } else {
                    tracing::warn!("worktree settle timed out before the TUI; continuing");
                }
                startup.push(StartupEvent::Ready(ready));
                break;
            }
            Some(event) => startup.push(event),
            None => {
                // A missing key is recoverable in the TUI — the replay opens
                // the wizard with the server's env var — so break with the
                // buffered events instead of exiting on the generic fallback.
                if startup
                    .iter()
                    .any(|event| matches!(event, StartupEvent::MissingApiKey { .. }))
                {
                    break;
                }
                // A config error or any other non-recoverable end: surface
                // the message and exit like Python (`Error: ...`, exit 1).
                if let Some(message) = startup.iter().find_map(|e| match e {
                    StartupEvent::ConfigError(m) => Some(m),
                    _ => None,
                }) {
                    return Err(anyhow::Error::msg(message.clone()));
                }
                return Err(anyhow::Error::msg(
                    "startup ended before the session was ready",
                ));
            }
        }
    }
    Ok(EarlyWorktreeStart {
        client,
        child,
        notifications: bridge_rx,
        crash_rx,
        ready: ready_rx,
        trust_tx,
        startup,
    })
}
