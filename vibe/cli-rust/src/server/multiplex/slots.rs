//! A multiplexed app-server's channels: opening, routing and closing them.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard};

use serde::Deserialize;
use serde_json::value::RawValue;
use tokio::sync::{mpsc, Notify};

use super::CLOSE_ACK_TIMEOUT;
use crate::server::process::Writer;

pub(super) struct Slots {
    to_child: Writer,
    state: Mutex<State>,
    // Signalled each time the last channel goes.
    emptied: Notify,
}

#[derive(Default)]
struct State {
    next_id: u64,
    channels: HashMap<u64, Slot>,
    ended: bool,
    retired: bool,
}

struct Slot {
    lines: mpsc::UnboundedSender<Vec<u8>>,
    // A frame reached the app-server, so it will acknowledge the close.
    opened: bool,
    closing: bool,
}

#[derive(Deserialize)]
struct Inbound<'a> {
    #[serde(rename = "channelId")]
    channel_id: String,
    #[serde(borrow)]
    message: Option<&'a RawValue>,
    error: Option<String>,
}

impl Slots {
    pub(super) fn new(to_child: Writer) -> Self {
        Self {
            to_child,
            state: Mutex::default(),
            emptied: Notify::new(),
        }
    }

    fn state(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|err| err.into_inner())
    }

    /// A new channel; on an ended app-server, one whose input is already closed.
    pub(super) fn register(&self, lines: mpsc::UnboundedSender<Vec<u8>>) -> u64 {
        self.add(lines, false).unwrap_or_default()
    }

    /// A new channel, or None once the app-server takes no new run.
    pub(super) fn try_register(&self, lines: mpsc::UnboundedSender<Vec<u8>>) -> Option<u64> {
        self.add(lines, true)
    }

    fn add(&self, lines: mpsc::UnboundedSender<Vec<u8>>, accepting: bool) -> Option<u64> {
        let mut state = self.state();
        if accepting && (state.ended || state.retired) {
            return None;
        }
        state.next_id += 1;
        let id = state.next_id;
        if !state.ended {
            let slot = Slot {
                lines,
                opened: false,
                closing: false,
            };
            state.channels.insert(id, slot);
        }
        Some(id)
    }

    /// Wait until the app-server serves no channel, then retire it.
    pub(super) async fn idle(&self) {
        loop {
            self.emptied.notified().await;
            let mut state = self.state();
            if state.channels.is_empty() {
                state.retired = true;
                return;
            }
        }
    }

    fn remove(&self, state: &mut State, id: u64) -> bool {
        let removed = state.channels.remove(&id).is_some();
        if removed && state.channels.is_empty() {
            self.emptied.notify_one();
        }
        removed
    }

    pub(super) fn load(&self) -> usize {
        self.state().channels.len()
    }

    pub(super) fn live(&self) -> usize {
        let state = self.state();
        state.channels.values().filter(|slot| !slot.closing).count()
    }

    pub(super) fn accepts_runs(&self) -> bool {
        let state = self.state();
        !state.ended && !state.retired
    }

    /// Send one of the channel's messages, `open` with the first; false once it closes.
    fn send(&self, id: u64, open: &mut Option<String>, message: &str) -> bool {
        let mut state = self.state();
        let Some(slot) = state.channels.get_mut(&id).filter(|slot| !slot.closing) else {
            return false;
        };
        slot.opened = true;
        let frame = match open.take() {
            Some(open) => format!(r#"{{"channelId":"{id}","open":{open},"message":{message}}}"#),
            None => format!(r#"{{"channelId":"{id}","message":{message}}}"#),
        };
        self.to_child.send(Some(frame)).is_ok()
    }

    /// Close the channel; it counts until the app-server acknowledges, or
    /// for `CLOSE_ACK_TIMEOUT` at most.
    pub(super) fn close(self: &Arc<Self>, id: u64) {
        let mut state = self.state();
        let Some(slot) = state.channels.get_mut(&id).filter(|slot| !slot.closing) else {
            return;
        };
        slot.closing = true;
        let close = format!(r#"{{"channelId":"{id}","message":null}}"#);
        let acknowledged = slot.opened && self.to_child.send(Some(close)).is_ok();
        let runtime = tokio::runtime::Handle::try_current();
        let (true, Ok(runtime)) = (acknowledged, runtime) else {
            self.remove(&mut state, id);
            return;
        };
        let slots = self.clone();
        let wait =
            crate::utils::timeout_or_env("VIBE_TEST_CLOSE_ACK_TIMEOUT_MS", CLOSE_ACK_TIMEOUT);
        runtime.spawn(async move {
            tokio::time::sleep(wait).await;
            slots.unacknowledged(id);
        });
    }

    fn unacknowledged(&self, id: u64) {
        let mut state = self.state();
        if self.remove(&mut state, id) {
            state.retired = true;
            tracing::warn!(
                channel = id,
                "The app-server did not acknowledge closing a run's channel; it takes no new run"
            );
        }
    }

    /// Hand one app-server frame to its channel; a close ends the channel.
    pub(super) fn route(&self, frame: &[u8]) {
        let Ok(frame) = serde_json::from_slice::<Inbound>(frame) else {
            tracing::warn!("Unparsable multiplexed app-server frame");
            return;
        };
        let Ok(id) = frame.channel_id.parse::<u64>() else {
            tracing::warn!("Multiplexed app-server frame with an invalid channel id");
            return;
        };
        let mut state = self.state();
        let Some(message) = frame.message else {
            if let Some(error) = frame.error {
                tracing::warn!(channel = id, "A run's app-server channel failed: {error}");
            }
            self.remove(&mut state, id);
            return;
        };
        if let Some(slot) = state.channels.get(&id) {
            let mut line = Vec::with_capacity(message.get().len() + 1);
            line.extend_from_slice(message.get().as_bytes());
            line.push(b'\n');
            let _ = slot.lines.send(line);
        }
    }

    /// An oversized frame is dropped, and the run it belonged to fails.
    pub(super) fn fail(self: &Arc<Self>, id: Option<u64>) {
        tracing::warn!(
            channel = id,
            "Dropped an oversized multiplexed app-server frame"
        );
        if let Some(id) = id {
            self.close(id);
            let mut state = self.state();
            self.remove(&mut state, id);
        }
    }

    /// The app-server ended: every channel's client sees its transport close.
    pub(super) fn end(&self) {
        let mut state = self.state();
        state.ended = true;
        state.channels.clear();
        self.emptied.notify_one();
    }

    pub(super) fn close_child(&self) {
        let _ = self.to_child.send(None);
    }
}

/// Wrap a client's frames for its channel until the client closes it.
pub(super) async fn forward(
    slots: Arc<Slots>,
    id: u64,
    open: String,
    mut from_client: mpsc::UnboundedReceiver<Option<String>>,
) {
    let mut open = Some(open);
    while let Some(Some(message)) = from_client.recv().await {
        if !slots.send(id, &mut open, &message) {
            // The channel's reader ends with it and fails this request.
            return;
        }
    }
    slots.close(id);
}
