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
use crate::server::Client;
use crate::server::{
    method, AgentConfig, ClientCapabilities, ClientInfo, InitializeParams, WorkspaceTrustDetails,
    CALLBACK_KINDS,
};
use crate::utils::startup_cache::StartupConfig;

use attach::attach_session;
use reads::{read_config, read_runtime};
use trust::resolve_workspace_trust;

pub use crate::utils::startup_cache::read_skills;
pub use attach::resume_params;
pub use launch::{launch_from_env, resolve_launch, resolve_launch_cwd, StartupRecorder};
pub use reads::{read_show_thinking_nodes, read_tokens, ConfigRead};

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
}

/// Initialize the connection and keep the UI degraded until the runtime is ready.
pub async fn handshake(
    client: Arc<Client>,
    event_tx: mpsc::Sender<StartupEvent>,
    cwd: Option<String>,
    show_unready_config: bool,
    resume: StartupResume,
    agent_config: AgentConfig,
    trust_rx: mpsc::Receiver<String>,
) {
    if let Err(error) = handshake_inner(
        client,
        &event_tx,
        cwd,
        show_unready_config,
        resume,
        agent_config,
        trust_rx,
    )
    .await
    {
        let _ = event_tx.send(StartupEvent::Failed(error.to_string())).await;
    }
}

async fn handshake_inner(
    client: Arc<Client>,
    event_tx: &mpsc::Sender<StartupEvent>,
    cwd: Option<String>,
    show_unready_config: bool,
    resume: StartupResume,
    agent_config: AgentConfig,
    mut trust_rx: mpsc::Receiver<String>,
) -> Result<()> {
    let init = InitializeParams {
        client_info: ClientInfo {
            name: "vibe-rs".into(),
            entrypoint: Some("cli".into()),
            version: env!("CARGO_PKG_VERSION").into(),
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
    let server_version = initialized
        .pointer("/serverInfo/version")
        .and_then(Value::as_str)
        .unwrap_or(env!("CARGO_PKG_VERSION"))
        .to_owned();
    client
        .notify(method::INITIALIZED, json!({}))
        .await
        .context("initialized")?;
    // `--trust` grants the session trust outright (Python `prompt_for_workspace_trust
    // = not trust_workspace`), so the gate would ask about a decision already taken.
    if !agent_config.trust_workspace {
        resolve_workspace_trust(&client, event_tx, &cwd, &mut trust_rx).await?;
    }

    // The saved sessions are read before the session is attached: the server
    // answers in order, so afterwards this cheap read would queue behind the
    // whole engine bring-up. The picker paints from it, `--continue` resolves
    // its target from it, and nothing else needs it.
    let mut sessions = None;
    let mut list_error = None;
    if matches!(resume, StartupResume::Picker | StartupResume::Continue) {
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
    let (state, attach) =
        attach_session(&client, resume, agent_config, continue_target, list_error).await?;
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
        })))
        .await
        .context("startup event receiver closed")?;
    Ok(())
}
