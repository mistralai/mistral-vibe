//! Async JSON-RPC client over the Vibe app-server stdio transport.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use anyhow::{anyhow, bail, Result};
use serde_json::Value;
use tokio::sync::{mpsc, oneshot, watch};

use super::child::ChildHandle;
pub use super::pending::Pending;
use crate::server::Request;
use crate::server::RpcError;

mod replay;
mod stub;
mod wire;

pub(crate) use wire::write_lines;
pub use wire::Writer;

/// Maximum notifications buffered (channel capacity). A full channel backpressures the reader.
pub(crate) const MAX_PENDING_NOTIFICATIONS: usize = 512;

/// A server -> client notification (method + params); the reducer lives in the TUI.
#[derive(Debug, Clone)]
pub struct Notification {
    pub method: String,
    pub params: Value,
}

/// The session the deny path falls back to when a callback omits `sessionId`.
pub type ActiveSession = Arc<Mutex<Option<String>>>;

#[derive(Debug)]
pub enum RequestFailure {
    Rpc(RpcError),
    AppServerClosed,
    /// The response exceeded `MAX_FRAME` (its size in bytes) and was dropped.
    FrameTooLarge(usize),
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
            Self::FrameTooLarge(len) => {
                write!(formatter, "app-server response too large ({len} bytes)")
            }
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
    replay_drained: AtomicU64,
    unsent_telemetry: AtomicU64,
    /// The app-server's own pid, from `initialize`'s `processId`; 0 before it.
    server_pid: AtomicU32,
}

/// How to launch the backend; defaults to `uv run --quiet --python
/// cpython>=3.12+gil vibe-app-server`.
#[derive(Clone)]
pub struct Launch {
    pub program: String,
    pub args: Vec<String>,
    pub cwd: Option<PathBuf>,
}

/// Params always passed to the app-server, whatever program launches it, so a
/// wheel install and a source checkout run the same backend.
pub const DEFAULT_APP_SERVER_ARGS: &[&str] = &[];

/// uv python request forcing a GIL build of a supported CPython: uv may select a
/// free-threaded build, for which the locked dependencies ship no wheels.
pub const UV_PYTHON_REQUEST: &str = "cpython>=3.12+gil";

impl Default for Launch {
    fn default() -> Self {
        // Source checkout: run the app-server through uv from the project root.
        Self {
            program: "uv".into(),
            args: [
                "run",
                "--quiet",
                "--python",
                UV_PYTHON_REQUEST,
                "vibe-app-server",
            ]
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
        let (child, stdin, stdout) = ChildHandle::launch(&launch, &[], &[])?;
        let (to_writer, writer_rx) = mpsc::unbounded_channel::<Option<String>>();
        // Single owner of stdin, so frame order follows event-handling order.
        wire::write_lines(stdin, writer_rx);
        let (client, notif_rx, crash_rx) = Client::wire(stdout, to_writer);
        Ok((client, child, notif_rx, crash_rx))
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
        let rx = self.pending.register(id);
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
            Ok(Err(failure @ RequestFailure::FrameTooLarge(_))) => {
                Err(RpcError::synthetic("transport", &failure.to_string()))
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
