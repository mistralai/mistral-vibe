//! Async JSON-RPC client over the Vibe app-server stdio transport.

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use anyhow::{anyhow, bail, Context, Result};
use serde_json::Value;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;
use tokio::sync::{mpsc, oneshot, watch};

use super::child::ChildHandle;
use crate::server::Request;
use crate::server::RpcError;

mod stub;

/// Maximum notifications buffered (channel capacity). A full channel backpressures the reader.
const MAX_PENDING_NOTIFICATIONS: usize = 512;

/// A server -> client notification (method + params); the reducer lives in the TUI.
#[derive(Debug, Clone)]
pub struct Notification {
    pub method: String,
    pub params: Value,
}

pub type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, RequestFailure>>>>>;
/// The session the deny path falls back to when a callback omits `sessionId`.
pub type ActiveSession = Arc<Mutex<Option<String>>>;

#[derive(Debug)]
pub enum RequestFailure {
    Rpc(RpcError),
    AppServerClosed,
}

impl RequestFailure {
    pub fn is_invalid_params(&self) -> bool {
        let Self::Rpc(error) = self else {
            return false;
        };
        error.code.as_str() == Some("invalid_params") || error.code.as_i64() == Some(-32602)
    }

    pub fn is_not_found(&self) -> bool {
        matches!(self, Self::Rpc(error) if error.code.as_str() == Some("not_found"))
    }
}

impl std::fmt::Display for RequestFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Rpc(error) => write!(formatter, "[{}] {}", error.code(), error.message),
            Self::AppServerClosed => formatter.write_str("app-server closed"),
        }
    }
}

impl std::error::Error for RequestFailure {}

/// Handle to a running app-server, with cloneable senders for requests.
pub struct Client {
    next_id: Arc<AtomicU64>,
    to_writer: mpsc::UnboundedSender<Option<String>>,
    pending: Pending,
    notif_tx: mpsc::Sender<Notification>,
    shutdown_requested: Arc<AtomicBool>,
    /// When true, server callbacks are denied instead of forwarded (headless).
    deny_callbacks: Arc<AtomicBool>,
    active_session: ActiveSession,
}

/// How to launch the backend; defaults to `uv run --quiet vibe-app-server --experimental-harness`.
#[derive(Clone)]
pub struct Launch {
    pub program: String,
    pub args: Vec<String>,
    pub cwd: Option<PathBuf>,
}

/// Params always passed to the app-server, whatever program launches it, so a
/// wheel install and a source checkout run the same backend.
pub const DEFAULT_APP_SERVER_ARGS: &[&str] = &["--experimental-harness"];

impl Default for Launch {
    fn default() -> Self {
        // Source checkout: run the app-server through uv from the project root.
        Self {
            program: "uv".into(),
            args: ["run", "--quiet", "vibe-app-server"]
                .into_iter()
                .map(str::to_owned)
                .chain(DEFAULT_APP_SERVER_ARGS.iter().map(|s| (*s).to_owned()))
                .collect(),
            cwd: None,
        }
    }
}

impl Client {
    /// Spawn the backend and wire up reader/writer tasks; caller drives the handshake.
    pub async fn spawn(
        launch: Launch,
    ) -> Result<(
        Client,
        ChildHandle,
        mpsc::Receiver<Notification>,
        watch::Receiver<bool>,
    )> {
        let mut cmd = Command::new(&launch.program);
        cmd.args(&launch.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            // Never inherited: the TUI owns the terminal, and anything the child
            // writes there corrupts cells ratatui does not know it must repaint.
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        // The child gets its own process group: the terminal sends Ctrl-C to
        // the whole foreground group, and a SIGINT landing on the app-server
        // mid-shutdown aborts it with a traceback. We drive its lifetime over
        // stdin instead.
        #[cfg(unix)]
        cmd.process_group(0);
        if let Some(cwd) = &launch.cwd {
            cmd.current_dir(cwd);
        }
        // The app-server child loads ~/.vibe/.env itself (python-dotenv); the
        // keys this client's non-interpolating parser set would shadow the
        // child's own values, so strip exactly those.
        for key in crate::credentials::dotenv::dotenv_set_keys() {
            cmd.env_remove(&key);
        }

        let mut child = ChildHandle::spawn(&mut cmd)
            .with_context(|| format!("failed to spawn `{}`", launch.program))?;

        let stdin = child.child.stdin.take().context("child stdin missing")?;
        let stdout = child.child.stdout.take().context("child stdout missing")?;
        if let Some(stderr) = child.child.stderr.take() {
            tokio::spawn(super::stderr::drain(stderr));
        }

        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let next_id = Arc::new(AtomicU64::new(1));
        let shutdown_requested = Arc::new(AtomicBool::new(false));
        let deny_callbacks = Arc::new(AtomicBool::new(false));
        let active_session: ActiveSession = Arc::new(Mutex::new(None));
        let (to_writer, mut writer_rx) = mpsc::unbounded_channel::<Option<String>>();
        let (notif_tx, notif_rx) = mpsc::channel::<Notification>(MAX_PENDING_NOTIFICATIONS);
        let (crash_tx, crash_rx) = watch::channel(false);

        // Writer task: single owner of stdin, preserves frame order. The
        // unbounded queue keeps `send_now` synchronous so frame order follows
        // event-handling order. `None` is the close sentinel: it drops stdin so
        // the server sees EOF; the reader task holds a sender clone until the
        // child exits, so waiting for all senders to drop would deadlock.
        tokio::spawn(async move {
            let mut stdin = stdin;
            while let Some(Some(line)) = writer_rx.recv().await {
                if stdin.write_all(line.as_bytes()).await.is_err() {
                    break;
                }
                if stdin.write_all(b"\n").await.is_err() {
                    break;
                }
                let _ = stdin.flush().await;
            }
        });

        // Reader task: route each line and answer server-initiated requests.
        super::reader::spawn(
            stdout,
            pending.clone(),
            notif_tx.clone(),
            to_writer.clone(),
            crash_tx,
            shutdown_requested.clone(),
            next_id.clone(),
            deny_callbacks.clone(),
            active_session.clone(),
        );

        Ok((
            Client {
                next_id,
                to_writer,
                pending,
                notif_tx,
                shutdown_requested,
                deny_callbacks,
                active_session,
            },
            child,
            notif_rx,
            crash_rx,
        ))
    }

    /// Send a typed request and await its result payload.
    pub async fn request(&self, method: &str, params: Value) -> Result<Value> {
        if method == crate::server::method::SESSION_STOP {
            self.shutdown_requested.store(true, Ordering::Relaxed);
        }
        let rx = self.send_now(method, params)?;
        match rx.await {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(error)) => Err(anyhow::Error::new(error).context(format!("{method} failed"))),
            Err(_) => bail!("{method}: response channel dropped"),
        }
    }

    /// Enqueue a request frame now and hand back its response channel, so the
    /// frame order follows event-handling order, not spawned-task poll order.
    pub fn send_now(
        &self,
        method: &str,
        params: Value,
    ) -> Result<oneshot::Receiver<Result<Value, RequestFailure>>> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = oneshot::channel();
        self.pending
            .lock()
            .unwrap_or_else(|err| err.into_inner())
            .insert(id, tx);

        let frame = serde_json::to_string(&Request::call(id, method, params))?;
        self.to_writer
            .send(Some(frame))
            .map_err(|_| anyhow!("writer task gone"))?;
        Ok(rx)
    }

    /// Like `request` but returns the structured `RpcError` (code + data).
    /// The error is a classified value callers inspect, not a chain to carry.
    #[allow(clippy::result_large_err)]
    pub async fn request_err(&self, method: &str, params: Value) -> Result<Value, RpcError> {
        let rx = self
            .send_now(method, params)
            .map_err(|error| RpcError::synthetic("send", &error.to_string()))?;
        match rx.await {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(RequestFailure::Rpc(error))) => Err(error),
            Ok(Err(RequestFailure::AppServerClosed)) => {
                Err(RpcError::synthetic("transport", "app-server closed"))
            }
            Err(_) => Err(RpcError::synthetic("transport", "response channel dropped")),
        }
    }

    /// A clone of the notification sender, so a client-side generator (e.g. the
    /// `/stress` firehose) can inject frames through the same reducer path real
    /// server notifications take.
    pub fn notif_sender(&self) -> mpsc::Sender<Notification> {
        self.notif_tx.clone()
    }

    /// When true, server `callback/call` requests are denied (headless mode).
    pub fn set_deny_callbacks(&self, deny: bool) {
        self.deny_callbacks.store(deny, Ordering::Relaxed);
    }

    /// The session the deny path substitutes when a callback omits `sessionId`.
    pub async fn set_active_session(&self, id: &str) {
        *self
            .active_session
            .lock()
            .unwrap_or_else(|err| err.into_inner()) = Some(id.to_owned());
    }

    /// Fire-and-forget notification (e.g. `initialized`).
    pub async fn notify(&self, method: &str, params: Value) -> Result<()> {
        let frame = serde_json::to_string(&Request::notify(method, params))?;
        self.to_writer
            .send(Some(frame))
            .map_err(|_| anyhow!("writer task gone"))?;
        Ok(())
    }

    /// Close child stdin after queued frames, so the server sees EOF and exits.
    /// The writer queue is unbounded, so this never blocks shutdown.
    pub async fn close_stdin(&self) {
        self.shutdown_requested.store(true, Ordering::Relaxed);
        let _ = self.to_writer.send(None);
    }
}
