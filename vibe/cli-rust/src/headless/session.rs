//! Opening the headless run's session: spawn the app-server, initialize it,
//! start the session, and start the turn.

use std::sync::{Arc, Mutex};

use anyhow::{Context, Result};
use serde_json::{json, Value};
use tokio::time::Instant;

use super::config::{agent_config, turn_id_from};
use super::drive::{RunState, WorkEnd};
use super::pre_turn;
use super::turn::run_turn_loop;
use crate::cli::{HeadlessOptions, HeadlessSession};
use crate::headless_output::Output;
use crate::headless_run::StopRequests;
use crate::headless_startup::{within_startup_deadline, STARTUP_TIMEOUT};
use crate::run_export::RunOutcome;
use crate::server::{
    method, Client, ClientCapabilities, ClientInfo, ContentBlock, InitializeParams, Launch,
    Notification, RpcError, SessionStartParams, TurnStartParams, CALLBACK_KINDS,
};

/// The headless run's session history page, as on `session/start`.
const HISTORY_LIMIT: u32 = 200;

/// The run's work: spawn the app-server, open the session, and run the
/// turn. Every failure is classified here, so the stop path can still
/// report it.
pub(super) async fn run_session(
    options: HeadlessOptions,
    cwd: Option<String>,
    launch: Launch,
    stop: Arc<StopRequests>,
    state: Arc<Mutex<RunState>>,
) -> WorkEnd {
    let (client, child, mut notifications, _crash_rx) = match Client::spawn(launch).await {
        Ok(spawned) => spawned,
        Err(error) => {
            return WorkEnd::Failed(
                RunOutcome::InfrastructureFailure,
                format!("spawn app-server: {error:#}"),
            );
        }
    };
    let client = Arc::new(client);
    client.set_deny_callbacks(true);
    {
        let mut state = state.lock().unwrap();
        state.client = Some(client.clone());
        state.child = Some(child);
    }

    // A worktree only comes with `session/start`, so a worktree run that
    // resumes starts first, then rebinds once the worktree has settled.
    let first = if options.worktree.is_some() {
        &HeadlessSession::New
    } else {
        &options.session
    };
    let started = within_startup_deadline(STARTUP_TIMEOUT, async {
        initialize(&client).await.context("initialize")?;
        Ok(open_session(&client, &options, cwd.clone(), first).await)
    })
    .await;
    let (mut session_id, mut session_cwd) = match started {
        Ok(Ok(opened)) => opened,
        Ok(Err(failure)) => return failure,
        Err(error) => {
            return WorkEnd::Failed(RunOutcome::InfrastructureFailure, format!("{error:#}"));
        }
    };
    state.lock().unwrap().session_id = Some(session_id.clone());
    client.set_active_session(&session_id).await;

    // Worktree settle wait.
    let mut settled: Vec<Notification> = Vec::new();
    if options.worktree.is_some() {
        let deadline = Instant::now() + crate::worktree_gate::SETTLE_WAIT;
        let settled_cwd =
            match crate::worktree_gate::absorb_until_settled(&mut notifications, deadline).await {
                Ok((settle, waited)) => {
                    if let Some(cwd) = &settle.cwd {
                        crate::worktree_gate::print_using(cwd);
                    } else {
                        tracing::warn!("worktree settle timed out; continuing");
                    }
                    settled = waited.notifications;
                    settle.cwd
                }
                Err(message) => {
                    return WorkEnd::Failed(RunOutcome::UsageError, message);
                }
            };
        if options.session != HeadlessSession::New {
            let cwd = settled_cwd.or(cwd.clone());
            (session_id, session_cwd) =
                match open_session(&client, &options, cwd, &options.session).await {
                    Ok(opened) => opened,
                    Err(failure) => return failure,
                };
            state.lock().unwrap().session_id = Some(session_id.clone());
            client.set_active_session(&session_id).await;
        }
    }

    // Wait for session ready.
    if let Err(error) = client
        .request(
            method::SESSION_READY_WAIT,
            json!({ "sessionId": session_id }),
        )
        .await
    {
        return WorkEnd::Failed(
            RunOutcome::InfrastructureFailure,
            format!("session/ready/wait: {error:#}"),
        );
    }

    let usage_baseline =
        match pre_turn::usage_baseline(&client, &options.session, &session_id).await {
            Ok(baseline) => baseline,
            Err(failure) => return failure,
        };
    state.lock().unwrap().usage_baseline = usage_baseline.clone();
    let (session_cwd, cwd_text) = (session_cwd.as_deref(), cwd.as_deref());
    if let Err(failure) =
        pre_turn::warn_if_host_untrusted(&client, &options, session_cwd, cwd_text).await
    {
        return failure;
    }

    // Read effective config for the export, as the session is ready and
    // before the turn spends anything.
    let config = match pre_turn::read_effective_config(&client, &session_id).await {
        Ok(config) => Some(config),
        Err(error) => {
            eprintln!("config/effective/read failed: {error:#}");
            None
        }
    };
    state.lock().unwrap().config = config;

    if let Some(failure) = pre_turn::pending_abort(&mut notifications, &mut settled, &stop) {
        return failure;
    }

    // Run the turn.
    let mut output = Output::new(options.output.clone());
    let turn = start_turn(&client, &options.prompt, &session_id).await;
    match turn {
        Ok(turn_id) => {
            state.lock().unwrap().turn_id = Some(turn_id.clone());
            let end = run_turn_loop(
                &client,
                &mut notifications,
                &mut output,
                &session_id,
                settled,
                &stop,
                &turn_id,
                &usage_baseline,
            )
            .await;
            WorkEnd::Turn(end)
        }
        Err(error) => WorkEnd::Failed(
            RunOutcome::InfrastructureFailure,
            format!("turn/start: {error:#}"),
        ),
    }
}

/// Send `turn/start` and return the turn id. The id rides the interrupt, so
/// a stop actually cuts the turn short.
async fn start_turn(client: &Arc<Client>, prompt: &str, session_id: &str) -> Result<String> {
    let params = TurnStartParams {
        idempotency_key: None,
        session_id: session_id.to_owned(),
        message: vec![ContentBlock::Text {
            text: prompt.to_owned(),
        }],
        injected: false,
        client_user_message_id: None,
        auto_title: None,
        user_display_content: None,
        mention_stats: None,
    };
    let value = serde_json::to_value(&params)?;
    let turn_response = client
        .request(method::TURN_START, value)
        .await
        .context("turn/start")?;
    turn_id_from(&turn_response)
}

async fn initialize(client: &Arc<Client>) -> Result<()> {
    let init = InitializeParams {
        client_info: ClientInfo {
            name: "vibe_programmatic".into(),
            entrypoint: Some("programmatic".into()),
            version: env!("CARGO_PKG_VERSION").into(),
            title: Some("Vibe programmatic CLI".into()),
            terminal_emulator: crate::terminal_detect::detect().into(),
        },
        capabilities: ClientCapabilities {
            callback_kinds: CALLBACK_KINDS.iter().map(|k| (*k).into()).collect(),
            ..Default::default()
        },
    };
    let value = serde_json::to_value(&init)?;
    client
        .request(method::INITIALIZE, value)
        .await
        .context("initialize")?;
    client
        .notify(method::INITIALIZED, json!({}))
        .await
        .context("initialized")?;
    Ok(())
}

/// Open the run's session: `session/start` for a new one, `session/continue`
/// or `session/resume` for a saved one. A failure is classified for the
/// export.
async fn open_session(
    client: &Arc<Client>,
    options: &HeadlessOptions,
    cwd: Option<String>,
    session: &HeadlessSession,
) -> Result<(String, Option<String>), WorkEnd> {
    let infrastructure = |error: anyhow::Error| {
        WorkEnd::Failed(RunOutcome::InfrastructureFailure, format!("{error:#}"))
    };
    let config = agent_config(options, cwd).map_err(infrastructure)?;
    let (lifecycle, params) = match session {
        HeadlessSession::New => {
            let params = SessionStartParams {
                agent_config: config,
                history_limit: HISTORY_LIMIT,
            };
            let params = serde_json::to_value(&params).map_err(|e| infrastructure(e.into()))?;
            (method::SESSION_START, params)
        }
        HeadlessSession::Continue => (
            method::SESSION_CONTINUE,
            json!({ "agentConfig": saved_session_config(&config), "historyLimit": HISTORY_LIMIT }),
        ),
        HeadlessSession::Resume(id) => (
            method::SESSION_RESUME,
            json!({
                "sessionId": id,
                "agentConfig": saved_session_config(&config),
                "historyLimit": HISTORY_LIMIT,
            }),
        ),
    };
    let result = client
        .request_err(lifecycle, params)
        .await
        .map_err(|error| WorkEnd::Failed(open_failure(&error), error.message))?;
    let session = result.pointer("/state/session");
    let field = |name: &str| session.and_then(|s| s.get(name)).and_then(Value::as_str);
    let id = field("id")
        .map(str::to_owned)
        .with_context(|| format!("{lifecycle} response missing session id"))
        .map_err(infrastructure)?;
    Ok((id, field("cwd").map(str::to_owned)))
}

/// The agent config sent to reopen a saved session. The server rejects
/// `worktree` outside `session/start`.
fn saved_session_config(config: &crate::server::AgentConfig) -> Value {
    let mut value = serde_json::to_value(config).unwrap_or_else(|_| json!({}));
    if let Some(fields) = value.as_object_mut() {
        fields.remove("worktree");
    }
    value
}

/// The outcome of a session that did not open: a missing API key or a config
/// that does not load is the config's fault, a request the server cannot
/// serve (an unknown session, nothing to continue) the caller's, and a failed
/// `--agent-socket` handshake as the server names it.
pub fn open_failure(error: &RpcError) -> RunOutcome {
    if let Some(outcome) = crate::agent_socket::named_outcome(error) {
        return outcome;
    }
    if error.is_unauthorized() || error.is_config_error() {
        return RunOutcome::ConfigError;
    }
    match error.code().as_str() {
        "invalid_params" | "not_found" => RunOutcome::UsageError,
        _ => RunOutcome::InfrastructureFailure,
    }
}
