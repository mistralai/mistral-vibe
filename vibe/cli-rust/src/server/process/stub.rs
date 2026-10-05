//! Test doubles for `Client`, with no app-server behind them.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::{Arc, Mutex};

use serde_json::Value;
use tokio::sync::mpsc;

use super::{Client, Notification, Pending};

impl Client {
    /// A `Client` whose writer and reader channels are immediately closed, so
    /// every `request` fails. Used only in tests that exercise pure reducer
    /// logic without a running app-server.
    #[doc(hidden)]
    pub fn stub() -> Self {
        let (to_writer, writer_rx) = mpsc::unbounded_channel::<Option<String>>();
        // Drop the writer receiver so `request` fails immediately.
        drop(writer_rx);
        Self::with_writer(to_writer, Arc::new(Mutex::new(HashMap::new())))
    }

    /// A `Client` that answers every request with `null` and hands each written
    /// frame to the returned receiver, so a test can assert what reached the wire.
    #[doc(hidden)]
    pub fn stub_answering() -> (Self, mpsc::UnboundedReceiver<Value>) {
        let (to_writer, mut writer_rx) = mpsc::unbounded_channel::<Option<String>>();
        let (frames_tx, frames_rx) = mpsc::unbounded_channel();
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let answers = pending.clone();
        tokio::spawn(async move {
            while let Some(Some(frame)) = writer_rx.recv().await {
                let Ok(frame) = serde_json::from_str::<Value>(&frame) else {
                    continue;
                };
                let responder = frame.get("id").and_then(Value::as_u64).and_then(|id| {
                    answers
                        .lock()
                        .unwrap_or_else(|err| err.into_inner())
                        .remove(&id)
                });
                let _ = frames_tx.send(frame);
                if let Some(responder) = responder {
                    let _ = responder.send(Ok(Value::Null));
                }
            }
        });
        (Self::with_writer(to_writer, pending), frames_rx)
    }

    fn with_writer(to_writer: mpsc::UnboundedSender<Option<String>>, pending: Pending) -> Self {
        let (notif_tx, _notif_rx) = mpsc::channel::<Notification>(1);
        Self {
            next_id: Arc::new(AtomicU64::new(1)),
            to_writer,
            pending,
            notif_tx,
            shutdown_requested: Arc::new(AtomicBool::new(false)),
            deny_callbacks: Arc::new(AtomicBool::new(false)),
            active_session: Arc::new(Mutex::new(None)),
        }
    }
}
