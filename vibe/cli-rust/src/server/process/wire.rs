//! Wiring a `Client` over any line transport: a child's pipes or a multiplexed channel.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64};
use std::sync::{Arc, Mutex};

use tokio::io::{AsyncRead, AsyncWrite, AsyncWriteExt};
use tokio::sync::{mpsc, watch};

use super::{ActiveSession, Client, Notification, Pending, MAX_PENDING_NOTIFICATIONS};

/// The client's outgoing frames; `None` closes the transport.
pub type Writer = mpsc::UnboundedSender<Option<String>>;

impl Client {
    /// A client that reads frames from `input` and queues its own on `to_writer`.
    pub(crate) fn wire<R>(
        input: R,
        to_writer: Writer,
    ) -> (Client, mpsc::Receiver<Notification>, watch::Receiver<bool>)
    where
        R: AsyncRead + Unpin + Send + 'static,
    {
        let pending = Pending::default();
        let next_id = Arc::new(AtomicU64::new(1));
        let shutdown_requested = Arc::new(AtomicBool::new(false));
        let deny_callbacks = Arc::new(AtomicBool::new(false));
        let active_session: ActiveSession = Arc::new(Mutex::new(None));
        let (notif_tx, notif_rx) = mpsc::channel::<Notification>(MAX_PENDING_NOTIFICATIONS);
        let (crash_tx, crash_rx) = watch::channel(false);
        crate::server::reader::spawn(
            input,
            pending.clone(),
            notif_tx.clone(),
            to_writer.clone(),
            crash_tx,
            shutdown_requested.clone(),
            next_id.clone(),
            deny_callbacks.clone(),
            active_session.clone(),
        );
        let client = Client {
            next_id,
            to_writer,
            pending,
            notif_tx,
            shutdown_requested,
            deny_callbacks,
            active_session,
            replay_drained: AtomicU64::new(0),
            unsent_telemetry: AtomicU64::new(0),
            server_pid: AtomicU32::new(0),
        };
        (client, notif_rx, crash_rx)
    }
}

/// Write each queued frame as one line, in order; `None` drops `output`, so
/// the reader sees EOF. The reader holds a sender clone until the transport
/// ends, so waiting for every sender to drop would deadlock.
pub(crate) fn write_lines<W>(output: W, mut frames: mpsc::UnboundedReceiver<Option<String>>)
where
    W: AsyncWrite + Unpin + Send + 'static,
{
    tokio::spawn(async move {
        let mut output = output;
        while let Some(Some(line)) = frames.recv().await {
            if output.write_all(line.as_bytes()).await.is_err() {
                break;
            }
            if output.write_all(b"\n").await.is_err() {
                break;
            }
            let _ = output.flush().await;
        }
    });
}
