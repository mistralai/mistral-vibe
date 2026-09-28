//! Headless (programmatic) mode: send one prompt, print the response, exit.
//! Mirrors Python `vibe/cli/programmatic.py`.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::cli::{HeadlessOptions, OutputFormat};
use crate::headless_history::read_history;
use crate::headless_output::{last_assistant_text, Output};
use crate::headless_trust::warn_if_untrusted;
use crate::server::signal;
use crate::server::{
    method, notification, AgentConfig, Client, ClientCapabilities, ClientInfo, ContentBlock,
    InitializeParams, Launch, Notification, SessionStartParams, TurnStartParams, CALLBACK_KINDS,
};
use crate::utils::paths::resolve_add_dirs;

/// Mirrors the Python `ProgrammaticLimitError` fallback text.
const LIMIT_REACHED: &str = "The configured conversation limit was reached";

/// Bound on the cleanup `session/stop` so a closed transport can't hang exit.
const SESSION_STOP_TIMEOUT: Duration = Duration::from_secs(5);

/// Mirrors Python `ProgrammaticLimitError`: printed bare on stderr, exit code 1.
#[derive(Debug)]
pub struct LimitError(pub String);

impl std::fmt::Display for LimitError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for LimitError {}

/// Entry point: spawn app-server, send one turn, print output, exit.
pub async fn run(options: HeadlessOptions, cwd: Option<String>, launch: Launch) -> Result<()> {
    let prompt = options.prompt.clone();
    tracing::info!("USER: {prompt}");

    // Arm the shutdown signals before the first await: until the handlers are
    // installed, the default actions apply and Ctrl-C, SIGTERM (`timeout`, job
    // cancellation), or SIGHUP would kill the process without running the
    // `kill_on_drop` teardown, orphaning the app-server and its tools. Once
    // armed, every phase maps to the Python `cli.py` KeyboardInterrupt path:
    // cleanup, then exit 0.
    let mut shutdown = signal::install().context("install signal handlers")?;
    let interrupt = shutdown.wait();
    tokio::pin!(interrupt);

    // `_child` keeps the app-server alive for the whole turn (the client talks
    // over its stdio); its Drop SIGKILLs the process group on every exit path.
    // `crash_rx` is unused here: a single short-lived turn has no live UI to
    // notify, and the child is torn down on return regardless.
    let (client, _child, mut notifications, _crash_rx) = tokio::select! {
        spawned = Client::spawn(launch) => spawned.context("spawn app-server")?,
        _ = &mut interrupt => return Ok(()),
    };
    let client = Arc::new(client);
    // Deny all server callbacks in headless mode (approval and user_input).
    client.set_deny_callbacks(true);

    // Session id as soon as the server creates one, so an interrupt during
    // startup can still stop it (same cleanup boundary as the turn phase).
    let created: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));

    let session_id = tokio::select! {
        started = async {
            initialize(&client).await.context("initialize")?;
            let session_id = start_session(&client, &options, cwd.clone()).await?;
            // Set before the value can win the select: an interrupt landing
            // here still sees the id and runs the bounded session/stop.
            created.lock().unwrap().replace(session_id.clone());
            // Callback denials fall back to this session when the request omits its id.
            client.set_active_session(&session_id).await;
            Ok::<_, anyhow::Error>(session_id)
        } => started?,
        _ = &mut interrupt => {
            stop_created_session(&client, &created).await;
            return Ok(());
        }
    };

    // From here on, session/stop must run on every exit path (ADR 0009).
    // Any shutdown signal cancels the turn but still falls through to the
    // bounded cleanup, so SIGINT/SIGTERM/SIGHUP never skip session/stop,
    // Sentry flush, or child teardown.
    let result = tokio::select! {
        r = run_session(
            &client,
            &mut notifications,
            &options,
            &prompt,
            &session_id,
            cwd.as_deref(),
        ) => r,
        _ = &mut interrupt => Ok(()),
    };

    // session/stop is the persistence + cleanup boundary (ADR 0009). Bound it so
    // an already-closed transport cannot hang the CLI, and surface its failure
    // only when the turn itself succeeded - a turn error stays primary.
    let stop = tokio::time::timeout(
        SESSION_STOP_TIMEOUT,
        client.request(method::SESSION_STOP, json!({"sessionId": &session_id})),
    )
    .await;

    match result {
        Err(turn_err) => Err(turn_err),
        Ok(()) => match stop {
            Ok(Ok(_)) => Ok(()),
            Ok(Err(err)) => Err(err.context("session/stop")),
            Err(_) => Err(anyhow!("session/stop timed out")),
        },
    }
}

/// Bounded session/stop for a session created before Ctrl-C ended startup.
/// Best-effort cleanup: failures are ignored, the process still exits 0
/// (Python `cli.py` KeyboardInterrupt path).
async fn stop_created_session(client: &Arc<Client>, created: &Arc<Mutex<Option<String>>>) {
    let Some(session_id) = created.lock().unwrap().take() else {
        return;
    };
    let _ = tokio::time::timeout(
        SESSION_STOP_TIMEOUT,
        client.request(method::SESSION_STOP, json!({ "sessionId": session_id })),
    )
    .await;
}

/// Run the turn and produce output, all before session/stop.
async fn run_session(
    client: &Arc<Client>,
    notifications: &mut mpsc::Receiver<Notification>,
    options: &HeadlessOptions,
    prompt: &str,
    session_id: &str,
    cwd: Option<&str>,
) -> Result<()> {
    client
        .request(method::SESSION_READY_WAIT, json!({"sessionId": session_id}))
        .await
        .context("session/ready/wait")?;

    // Python warns on stderr before the turn when the workspace is untrusted.
    warn_if_untrusted(client, cwd).await?;

    let mut output = Output::new(options.output.clone());
    let result =
        start_turn_and_consume(client, notifications, &mut output, prompt, session_id).await;

    // Only finalize on success; on error the message goes to stderr (Python programmatic.py).
    if let Err(ref err) = result {
        // Python raises `ProgrammaticLimitError` with the last assistant text; main prints it bare.
        if err.downcast_ref::<LimitError>().is_some() {
            let text = match read_history(client, session_id).await {
                Ok(entries) => {
                    last_assistant_text(&entries).unwrap_or_else(|| LIMIT_REACHED.to_owned())
                }
                Err(_) => LIMIT_REACHED.to_owned(),
            };
            return Err(LimitError(text).into());
        }
        return result;
    }

    if options.output == OutputFormat::Streaming {
        return Ok(());
    }

    // Success: re-read history and emit output before session/stop.
    let history_entries = read_history(client, session_id)
        .await
        .context("session/history/list")?;
    output.finalize(history_entries).context("write output")?;
    Ok(())
}

async fn initialize(client: &Arc<Client>) -> Result<()> {
    let init = InitializeParams {
        client_info: ClientInfo {
            name: "vibe_programmatic".into(),
            entrypoint: Some("programmatic".into()),
            version: env!("CARGO_PKG_VERSION").into(),
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

async fn start_session(
    client: &Arc<Client>,
    options: &HeadlessOptions,
    cwd: Option<String>,
) -> Result<String> {
    let agent_config = agent_config(options, cwd)?;
    let params = SessionStartParams {
        agent_config,
        history_limit: 200,
    };
    let value = serde_json::to_value(&params)?;
    let result = client
        .request(method::SESSION_START, value)
        .await
        .context("session/start")?;
    let id = result
        .pointer("/state/session/id")
        .and_then(Value::as_str)
        .context("session/start response missing session id")?;
    Ok(id.to_owned())
}

/// Headless `agentConfig`: `--yolo` is a session-wide bypass, never a swap onto `auto-approve`.
pub fn agent_config(options: &HeadlessOptions, cwd: Option<String>) -> Result<AgentConfig> {
    Ok(AgentConfig {
        cwd,
        agent: options.agent.clone(),
        auto_approve: options.auto_approve,
        enabled_tools: options.enabled_tools.clone(),
        disabled_tools: build_disabled_tools(&options.disabled_tools),
        max_turns: options.max_turns,
        max_price: options.max_price,
        max_session_tokens: options.max_tokens,
        headless: true,
        trust_workspace: options.trust,
        workspace_roots: resolve_add_dirs(&options.add_dir)?,
    })
}

pub fn build_disabled_tools(explicit: &[String]) -> Vec<String> {
    let mut tools = explicit.to_vec();
    for &t in &["ask_user_question", "exit_plan_mode"] {
        if !tools.iter().any(|x| x == t) {
            tools.push(t.to_owned());
        }
    }
    tools
}

async fn start_turn_and_consume(
    client: &Arc<Client>,
    notifications: &mut mpsc::Receiver<Notification>,
    output: &mut Output,
    prompt: &str,
    session_id: &str,
) -> Result<()> {
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
    let turn_id = turn_id_from(&turn_response)?;

    loop {
        let notification = notifications
            .recv()
            .await
            .ok_or_else(|| anyhow!("app-server closed"))?;
        match notification.method.as_str() {
            notification::HISTORY_ENTRY_ADDED | notification::HISTORY_ENTRY_UPDATED => {
                if let Some(entry) = output.consume_entry(&notification.params)? {
                    output.emit_entry(&entry).context("write streaming entry")?;
                }
            }
            notification::TURN_COMPLETED if matches_turn(&notification.params, &turn_id) => {
                return handle_turn_completion(client, &notification.params, session_id).await;
            }
            notification::SERVER_DISCONNECTED => bail!("app-server closed"),
            // The server rejected a callback denial; the turn can never finish.
            notification::CALLBACK_RESULT_FAILED => {
                bail!("{}", notification.params)
            }
            _ => {}
        }
    }
}

async fn handle_turn_completion(
    _client: &Arc<Client>,
    params: &Value,
    _session_id: &str,
) -> Result<()> {
    let turn = params.pointer("/turn").unwrap_or(&Value::Null);
    let status = turn.get("status").and_then(Value::as_str).unwrap_or("");
    let stop_reason = turn.get("stopReason").and_then(Value::as_str);

    if status == "failed" {
        let message = turn
            .pointer("/error/message")
            .and_then(Value::as_str)
            .unwrap_or("Turn failed");
        bail!("{message}");
    }

    if stop_reason == Some("limit") {
        return Err(LimitError(LIMIT_REACHED.to_owned()).into());
    }

    Ok(())
}

/// A missing or non-string id must fail the run, not default to "" and hang.
pub fn turn_id_from(response: &Value) -> Result<String> {
    response
        .pointer("/turn/id")
        .and_then(Value::as_str)
        .context("turn/start response missing turn id")
        .map(str::to_owned)
}

fn matches_turn(params: &Value, turn_id: &str) -> bool {
    params.pointer("/turn/id").and_then(Value::as_str) == Some(turn_id)
}
