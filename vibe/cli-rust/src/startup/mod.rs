//! Startup orchestration: the connection handshake and what it hands the UI.

mod attach;
pub mod banners;
mod launch;
mod reads;
mod trust;

use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::cli::StartupResume;
use crate::server::{
    method, AgentConfig, ClientCapabilities, ClientInfo, InitializeParams, WorkspaceTrustDetails,
    CALLBACK_KINDS,
};
use crate::server::{Client, Notification};
use crate::utils::startup_cache::StartupConfig;
use crate::worktree::WorktreeInfo;

use attach::{attach_session, settle_worktree_session};
use reads::{read_config, read_runtime};
use trust::resolve_workspace_trust;

/// A worktree run's notification stream, handed to the handshake: it absorbs
/// the stream until the worktree settles (the resume is scoped to the worktree
/// directory, which only exists then) and forwards the rest to the bridge the
/// event loop reads.
pub type WorktreeNotifications = (mpsc::Receiver<Notification>, mpsc::Sender<Notification>);

/// `ClientInfo.name`, the server's telemetry `client_name` (Python sends `vibe_tui`).
pub const CLIENT_NAME: &str = "vibe_rs";

pub use crate::utils::startup_cache::read_skills;
pub use attach::resume_params;
pub use launch::{
    agent_ready_duration_ms, first_frame_duration_ms, launch_from_env, mark_first_draw,
    resolve_launch, resolve_launch_cwd, StartupRecorder,
};
pub use reads::{
    read_ask_confirmation_on_exit, read_show_subagent_status_list, read_show_thinking_nodes,
    read_tokens, ConfigRead,
};

pub enum StartupEvent {
    /// The cwd is untrusted: the gate must answer before the session opens.
    Trust(Box<WorkspaceTrustDetails>),
    Attached(String),
    /// Raw `session/list`, read before `session/start` so `--resume` can show
    /// its picker without waiting for the engine to come up.
    Sessions(Box<Value>),
    Config(Box<StartupConfig>),
    Ready(Box<Ready>),
    Failed(String),
    /// Missing API key; the pre-session wizard round must run. `env_key`
    /// is the server's derived env var name, surfaced with the guard's
    /// error when the wizard cannot break the loop.
    MissingApiKey {
        provider: String,
        env_key: Option<String>,
    },
    /// Invalid configuration; the message says what is wrong.
    ConfigError(String),
}

/// What attaching the session did, as one transcript notice.
pub enum Attach {
    /// A fresh `session/start`.
    Started,
    /// `--continue`/`--resume <id>` attached this saved session.
    Resumed(String),
    /// `--continue` found nothing to resume; a fresh session was started.
    NoPrevious,
    /// The resume failed and a fresh session was started instead.
    Failed(String),
}

/// What the handshake hands the UI once the startup RPCs settle.
pub struct Ready {
    /// Authoritative first-frame values from `runtime/read`, also re-cached to disk.
    pub startup_cache: StartupConfig,
    /// The live `config/read` values; never cached, unlike `startup_cache`.
    pub config: ConfigRead,
    /// Raw state of the attached session; the UI parses it once.
    pub state: Value,
    /// How that session was attached, for the startup notice.
    pub attach: Attach,
    /// Raw `runtime/read` value; the reducer projects it with the `read_*` helpers.
    pub runtime: Value,
    /// No cached startup config: this launch paid the full first-frame cost.
    pub is_cold_start: bool,
    /// The moved session cwd, when a `--worktree` run settled: the pre-TUI
    /// `Using worktree` line prints it and the resume was scoped to it.
    pub settled: Option<String>,
    /// This run's prepared worktree, pinned for the exit cleanup (a resumed
    /// session's restored effect carries the original run's `created`).
    pub prepared: Option<WorktreeInfo>,
    /// Notifications absorbed while waiting for the settle, replayed into the
    /// app before its first frame.
    pub absorbed: Vec<Notification>,
}

/// The per-run handshake inputs.
pub struct HandshakeParams {
    pub cwd: Option<String>,
    pub show_unready_config: bool,
    pub resume: StartupResume,
    pub agent_config: AgentConfig,
    /// A worktree run's notification stream, handed to the settle gate and
    /// bridged back to the event loop afterwards.
    pub notifications: Option<WorktreeNotifications>,
    /// The server version of an `initialize` the caller already ran on this
    /// connection (the wizard round's `setup/*` pre-session surface):
    /// `initialize` may only be called once, so the handshake skips it.
    pub pre_initialized: Option<String>,
    /// The trust gate already ran on this connection and its decision was
    /// persisted server-side: the retried handshake after the onboarding
    /// wizard skips the gate. Never derive from `pre_initialized` — the
    /// pre-loop round's first handshake also sets it and must be gated.
    pub trust_already_resolved: bool,
}

/// Initialize the connection and return the server's version. Shared by the
/// handshake and the setup round, which runs it before its `setup/*` calls
/// (those are valid on an initialized connection, before any session).
pub async fn initialize_connection(client: &Client) -> Result<String> {
    let init = InitializeParams {
        client_info: ClientInfo {
            name: CLIENT_NAME.into(),
            entrypoint: Some("cli".into()),
            version: env!("CARGO_PKG_VERSION").into(),
            title: Some("Vibe Rust".into()),
            terminal_emulator: crate::terminal_detect::detect().into(),
        },
        capabilities: ClientCapabilities {
            callback_kinds: CALLBACK_KINDS.iter().map(|k| (*k).into()).collect(),
            ..Default::default()
        },
    };
    let value = serde_json::to_value(&init)?;
    let initialized = client
        .request(method::INITIALIZE, value)
        .await
        .context("initialize")?;
    client
        .notify(method::INITIALIZED, json!({}))
        .await
        .context("initialized")?;
    Ok(initialized
        .pointer("/serverInfo/version")
        .and_then(Value::as_str)
        .unwrap_or(env!("CARGO_PKG_VERSION"))
        .to_owned())
}

/// Initialize the connection and keep the UI degraded until the runtime is ready.
pub async fn handshake(
    client: Arc<Client>,
    event_tx: mpsc::Sender<StartupEvent>,
    trust_rx: mpsc::Receiver<String>,
    params: HandshakeParams,
) {
    if let Err(error) = handshake_inner(client, &event_tx, trust_rx, params).await {
        // The full error chain, not just the outer context: a bare
        // "session/start" hides whether the server rejected the request or
        // the process died, which is the first question at every triage.
        let _ = event_tx
            .send(StartupEvent::Failed(format!("{error:#}")))
            .await;
    }
}

async fn handshake_inner(
    client: Arc<Client>,
    event_tx: &mpsc::Sender<StartupEvent>,
    mut trust_rx: mpsc::Receiver<String>,
    params: HandshakeParams,
) -> Result<()> {
    let HandshakeParams {
        cwd,
        show_unready_config,
        resume,
        agent_config,
        mut notifications,
        pre_initialized,
        trust_already_resolved,
    } = params;
    // The wizard round already initialized this connection for `setup/*`;
    // `initialize` may only be called once, so only a fresh child runs it.
    let server_version = match pre_initialized {
        Some(version) => version,
        None => initialize_connection(&client).await?,
    };
    // `--trust` grants the session trust outright (Python `prompt_for_workspace_trust
    // = not trust_workspace`), so the gate would ask about a decision already taken.
    // The wizard round's retry skips the gate: its answer was given on the
    // first pass and persisted, and the server reports an explicitly-untrusted
    // cwd again (`include_explicitly_untrusted=True`), so re-prompting would
    // ask about a decision already made.
    if !agent_config.trust_workspace && !trust_already_resolved {
        resolve_workspace_trust(&client, event_tx, &cwd, &mut trust_rx).await?;
    }

    let worktree = agent_config.worktree.is_some();

    // The saved sessions are read before the session is attached: the server
    // answers in order, so afterwards this cheap read would queue behind the
    // whole engine bring-up. The picker paints from it, `--continue` resolves
    // its target from it, and nothing else needs it. A worktree run cannot
    // scope its list yet — the worktree does not exist until the app-server
    // prepares it — so the list moves after the settle, scoped to the
    // worktree directory (Python chdirs before its lookups; the settled cwd
    // is the thin-client equivalent).
    let mut sessions = None;
    let mut list_error = None;
    if !worktree && matches!(resume, StartupResume::Picker | StartupResume::Continue) {
        match client
            .request(method::SESSION_LIST, json!({"cwd": cwd}))
            .await
        {
            Ok(value) => sessions = Some(value),
            // Without the list neither intent can be honoured, so the notice
            // says that rather than claiming there was nothing to resume.
            Err(error) => list_error = Some(format!("Failed to list sessions: {error}")),
        }
    }
    let continue_target = sessions
        .as_ref()
        .and_then(|value| value.get("continueSessionId"))
        .and_then(Value::as_str)
        .map(str::to_owned);
    if let (StartupResume::Picker, Some(value)) = (&resume, sessions) {
        event_tx
            .send(StartupEvent::Sessions(Box::new(value)))
            .await
            .context("startup event receiver closed")?;
    }

    let cwd_owned = cwd.clone();
    let (mut state, mut attach) = if worktree {
        // A worktree run starts bare: the session is rebound onto its
        // worktree after the settle below.
        let started = match attach::start_session(&client, agent_config.clone()).await {
            Ok(started) => started,
            Err(error) => return handle_start_error(error, event_tx).await,
        };
        (started, Attach::Started)
    } else {
        match attach_session(
            &client,
            resume.clone(),
            agent_config.clone(),
            continue_target.clone(),
            list_error.clone(),
        )
        .await
        {
            Ok(result) => result,
            Err(error) => return handle_start_error(error, event_tx).await,
        }
    };

    // A worktree run waits its worktree out before anything that depends on
    // where the session runs: the settle carries the directory the resume
    // scopes to, and the runtime reads follow the rebind.
    let mut settled: Option<String> = None;
    let mut prepared: Option<WorktreeInfo> = None;
    let mut absorbed: Vec<Notification> = Vec::new();
    if worktree {
        let (rebound, reattached, cwd, worktree, replayed) = settle_worktree_session(
            &client,
            event_tx,
            notifications.take(),
            &resume,
            &agent_config,
            continue_target,
            list_error,
            state,
        )
        .await?;
        settled = cwd;
        prepared = worktree;
        absorbed = replayed;
        state = rebound;
        attach = reattached;
    }

    let session_id = state
        .pointer("/session/id")
        .and_then(Value::as_str)
        .context("attached session state missing session id")?
        .to_owned();
    event_tx
        .send(StartupEvent::Attached(session_id.clone()))
        .await
        .context("startup event receiver closed")?;

    let mut runtime = read_runtime(&client, &session_id).await?;
    if runtime.get("ready").and_then(Value::as_bool) == Some(false) {
        if show_unready_config {
            if let Some(config) = StartupConfig::from_runtime(&server_version, &runtime) {
                event_tx
                    .send(StartupEvent::Config(Box::new(config)))
                    .await
                    .context("startup event receiver closed")?;
            }
        }
        client
            .request(method::SESSION_READY_WAIT, json!({"sessionId": session_id}))
            .await
            .context("session/ready/wait")?;
        runtime = read_runtime(&client, &session_id).await?;
    }
    let startup_cache = StartupConfig::from_runtime(&server_version, &runtime)
        .context("runtime/read response missing startup config")?;
    let config = read_config(&client, cwd_owned).await;

    event_tx
        .send(StartupEvent::Ready(Box::new(Ready {
            startup_cache,
            config,
            state,
            attach,
            runtime,
            is_cold_start: show_unready_config,
            settled,
            prepared,
            absorbed,
        })))
        .await
        .context("startup event receiver closed")?;
    Ok(())
}

/// A `session/start` failure the onboarding wizard can handle: auth opens the
/// key wizard (the startup is over for this run, Ok), config errors surface
/// as a notice, anything else fails the startup.
async fn handle_start_error(
    error: attach::StartError,
    event_tx: &mpsc::Sender<StartupEvent>,
) -> Result<()> {
    match error {
        attach::StartError::Auth(err) => {
            let data = err.data.as_ref();
            let str_field = |name: &str| {
                data.and_then(|d| d.get(name))
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            };
            let provider = str_field("provider").unwrap_or_else(|| "unknown".into());
            let env_key = str_field("env_key").filter(|key| !key.is_empty());
            // The server resolved the provider against the full effective
            // config (project layer included); the wizard round names it
            // back to `setup/status` so the seed matches the failure.
            let _ = event_tx
                .send(StartupEvent::MissingApiKey { provider, env_key })
                .await;
            Ok(())
        }
        attach::StartError::Config(err) => {
            let _ = event_tx.send(StartupEvent::ConfigError(err.message)).await;
            Ok(())
        }
        attach::StartError::Other(error) => Err(error),
    }
}
