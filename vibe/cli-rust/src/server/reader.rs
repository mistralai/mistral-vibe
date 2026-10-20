//! The stdout reader task: frame splitting, response routing, backpressure.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

use serde_json::Value;
use tokio::io::{AsyncRead, AsyncReadExt};
use tokio::sync::{mpsc, watch};

use super::frames::Frames;
use super::process::{ActiveSession, Pending, RequestFailure};
use super::{notification, Incoming, Notification};

/// Parked-notification byte cap: past it the event loop is wedged, and the
/// transport fails decisively instead of growing without bound.
const BACKLOG_MAX_BYTES: usize = 8 * 1024 * 1024;

/// Notification sink for the reader: a full channel parks frames in a
/// bounded backlog instead of blocking, so responses keep routing.
struct NotifSink {
    tx: mpsc::Sender<Notification>,
    backlog: VecDeque<(usize, Notification)>,
    backlog_bytes: usize,
}

impl NotifSink {
    /// Park one notification if the channel is full. False: stop reading.
    fn push(&mut self, n: Notification, size_hint: usize) -> bool {
        if self.backlog.is_empty() {
            match self.tx.try_send(n) {
                Ok(()) => return true,
                Err(tokio::sync::mpsc::error::TrySendError::Full(n)) => {
                    return self.park(n, size_hint)
                }
                Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => return false,
            }
        }
        self.park(n, size_hint)
    }

    fn park(&mut self, n: Notification, size_hint: usize) -> bool {
        if self.backlog_bytes + size_hint > BACKLOG_MAX_BYTES {
            tracing::error!(
                bytes = self.backlog_bytes,
                "notification backlog exceeds its cap; protocol error"
            );
            return false;
        }
        self.backlog_bytes += size_hint;
        self.backlog.push_back((size_hint, n));
        true
    }

    fn flush(&mut self) -> bool {
        while let Some((size, n)) = self.backlog.pop_front() {
            match self.tx.try_send(n) {
                Ok(()) => {
                    self.backlog_bytes -= size;
                }
                Err(tokio::sync::mpsc::error::TrySendError::Full(n)) => {
                    self.backlog.push_front((size, n));
                    return true;
                }
                Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => return false,
            }
        }
        true
    }
}

/// Split the next newline-terminated frame off `tail`; `searched` marks how
/// far previous reads already scanned, so the scan stays linear across
/// reads. None: no complete frame yet.
pub fn next_frame(tail: &mut Vec<u8>, searched: &mut usize) -> Option<String> {
    let pos = match (*searched..tail.len()).find(|&i| tail[i] == b'\n') {
        Some(pos) => pos,
        None => {
            *searched = tail.len();
            return None;
        }
    };
    let frame = String::from_utf8_lossy(&tail[..pos])
        .trim_end_matches('\r')
        .to_string();
    tail.drain(..=pos);
    *searched = 0;
    Some(frame)
}

/// Route one non-empty frame; false stops the reader.
#[allow(clippy::too_many_arguments)]
async fn accept_frame(
    frame: &str,
    pending: &Pending,
    sink: &mut NotifSink,
    to_writer: &mpsc::UnboundedSender<Option<String>>,
    next_request_id: &AtomicU64,
    deny_callbacks: &AtomicBool,
    active_session: &ActiveSession,
) -> bool {
    if frame.trim().is_empty() {
        return true;
    }
    route_line(
        frame,
        pending,
        sink,
        to_writer,
        next_request_id,
        deny_callbacks,
        active_session,
    )
    .await
}

/// Spawn the reader task that routes each input line and answers callbacks.
#[allow(clippy::too_many_arguments)]
pub(super) fn spawn<R: AsyncRead + Unpin + Send + 'static>(
    mut stdout: R,
    pending: Pending,
    notif_tx: mpsc::Sender<Notification>,
    to_writer: mpsc::UnboundedSender<Option<String>>,
    crash_tx: watch::Sender<bool>,
    shutdown_requested: Arc<AtomicBool>,
    next_request_id: Arc<AtomicU64>,
    deny_callbacks: Arc<AtomicBool>,
    active_session: ActiveSession,
) {
    tokio::spawn(async move {
        let mut sink = NotifSink {
            tx: notif_tx,
            backlog: VecDeque::new(),
            backlog_bytes: 0,
        };
        let mut frames = Frames::default();
        let mut chunk = vec![0u8; 16384];
        let mut flush_tick = tokio::time::interval(std::time::Duration::from_millis(20));
        let mut reading = true;
        while reading {
            tokio::select! {
                read = stdout.read(&mut chunk) => match read {
                    Ok(0) => {
                        // EOF: route a final unterminated frame, like lines() did.
                        if let Some(frame) = frames.finish(&pending, &to_writer) {
                            reading = accept_frame(
                                &frame,
                                &pending,
                                &mut sink,
                                &to_writer,
                                &next_request_id,
                                &deny_callbacks,
                                &active_session,
                            )
                            .await;
                        } else {
                            reading = false;
                        }
                    }
                    Ok(n) => {
                        for frame in frames.push(&chunk[..n], &pending, &to_writer) {
                            if !accept_frame(
                                &frame,
                                &pending,
                                &mut sink,
                                &to_writer,
                                &next_request_id,
                                &deny_callbacks,
                                &active_session,
                            )
                            .await
                            {
                                reading = false;
                                break;
                            }
                        }
                    }
                    Err(err) => {
                        tracing::warn!(%err, "stdout read error");
                        reading = false;
                    }
                },
                _ = flush_tick.tick() => {
                    if !sink.flush() {
                        reading = false;
                    }
                },
            }
        }
        let crashed = !shutdown_requested.load(Ordering::Relaxed);
        let _ = crash_tx.send(crashed);
        pending.close();
        // Wake any consumer blocked on the notification channel: the `Client`
        // keeps a `notif_tx` clone alive, so `recv()` would otherwise hang after
        // the reader exits (e.g. the headless turn loop mid-turn).
        let _ = sink
            .tx
            .send(Notification {
                method: notification::SERVER_DISCONNECTED.into(),
                params: Value::Null,
            })
            .await;
    });
}

#[allow(clippy::too_many_arguments)]
async fn route_line(
    line: &str,
    pending: &Pending,
    sink: &mut NotifSink,
    to_writer: &mpsc::UnboundedSender<Option<String>>,
    next_request_id: &AtomicU64,
    deny_callbacks: &AtomicBool,
    active_session: &ActiveSession,
) -> bool {
    let incoming: Incoming = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(err) => {
            tracing::warn!(%err, line, "unparsable frame");
            return true;
        }
    };

    match (incoming.id, incoming.method) {
        // Response to one of our requests.
        (Some(id), None) => {
            if let Some(tx) = pending.take(id) {
                let result = match incoming.error {
                    Some(error) => Err(RequestFailure::Rpc(error)),
                    None => Ok(incoming.result.unwrap_or(Value::Null)),
                };
                let _ = tx.send(result);
            }
        }
        // Server-initiated request (see `callback` for the kinds we answer).
        (Some(id), Some(method)) => {
            let wiring = super::callback::DenyWiring {
                pending,
                notif_tx: &sink.tx,
                to_writer,
                next_request_id,
                active_session,
            };
            if let Some(n) = super::callback::handle_server_request(
                id,
                &method,
                incoming.params,
                wiring,
                deny_callbacks.load(Ordering::Relaxed),
            )
            .await
            {
                if !sink.push(n, line.len()) {
                    return false;
                }
            }
        }
        // Notification.
        (None, Some(method)) => {
            let n = Notification {
                method,
                params: incoming.params.unwrap_or(Value::Null),
            };
            if !sink.push(n, line.len()) {
                return false;
            }
        }
        (None, None) => tracing::warn!(line, "frame with neither id nor method"),
    }
    true
}
