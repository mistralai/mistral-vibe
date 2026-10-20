//! One app-server serving many runs, one `{"channelId", "open"?, "message"}` channel each.

mod input;
mod slots;

use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::Result;
use serde_json::Value;
use tokio::sync::mpsc;

use self::input::{channel_of, ChannelInput, MAX_LINE};
use self::slots::Slots;
use super::lines::{Line, LineReader};
use super::process::write_lines;
use super::{ChildHandle, Client, Connected, Connection, Launch};

/// How long a closed channel waits for the app-server's acknowledgement.
pub const CLOSE_ACK_TIMEOUT: Duration = Duration::from_secs(15);

/// An app-server process started with `--multiplex`, and its open channels.
pub struct MuxClient {
    slots: Arc<Slots>,
    child: Mutex<Option<ChildHandle>>,
}

/// Keeps a channel open; dropping it closes the channel.
pub struct ChannelGuard {
    slots: Arc<Slots>,
    id: u64,
}

impl MuxClient {
    /// Start the app-server; no channel opens until `open_channel`.
    pub fn spawn(launch: &Launch) -> Result<Self> {
        let (child, stdin, stdout) =
            ChildHandle::launch(launch, &["--multiplex"], &app_server_env())?;
        let (to_child, from_slots) = mpsc::unbounded_channel();
        write_lines(stdin, from_slots);
        let slots = Arc::new(Slots::new(to_child));
        tokio::spawn(demux(stdout, slots.clone()));
        Ok(Self {
            slots,
            child: Mutex::new(Some(child)),
        })
    }

    /// A client for one run, served by its own channel. `open` is sent with
    /// the channel's first message; it fails as a closed app-server once this
    /// one has ended.
    pub fn open_channel(&self, open: &Value) -> Connected {
        let (lines_tx, lines_rx) = mpsc::unbounded_channel();
        let id = self.slots.register(lines_tx);
        self.wire(id, lines_rx, open)
    }

    /// As `open_channel`, or None once the app-server takes no new run.
    pub fn try_open_channel(&self, open: &Value) -> Option<Connected> {
        let (lines_tx, lines_rx) = mpsc::unbounded_channel();
        let id = self.slots.try_register(lines_tx)?;
        Some(self.wire(id, lines_rx, open))
    }

    fn wire(&self, id: u64, lines_rx: mpsc::UnboundedReceiver<Vec<u8>>, open: &Value) -> Connected {
        let (to_writer, from_client) = mpsc::unbounded_channel();
        tokio::spawn(slots::forward(
            self.slots.clone(),
            id,
            open.to_string(),
            from_client,
        ));
        let (client, notifications, _crash) = Client::wire(ChannelInput::new(lines_rx), to_writer);
        Connected {
            client,
            connection: Connection::Channel(ChannelGuard {
                slots: self.slots.clone(),
                id,
            }),
            notifications,
        }
    }

    /// Channels opened and not yet acknowledged closed.
    pub fn load(&self) -> usize {
        self.slots.load()
    }

    /// Channels opened and not yet closed: the runs it serves. A closed
    /// channel awaiting its acknowledgement is not one.
    pub fn live_runs(&self) -> usize {
        self.slots.live()
    }

    /// False once the app-server ended, went idle or left a close
    /// unacknowledged: it takes no new run.
    pub fn accepts_runs(&self) -> bool {
        self.slots.accepts_runs()
    }

    /// Wait until the app-server serves no channel; it then takes no new run.
    pub async fn idle(&self) {
        self.slots.idle().await;
    }

    pub fn pid(&self) -> Option<u32> {
        self.child
            .lock()
            .unwrap()
            .as_ref()
            .and_then(ChildHandle::pid)
    }

    /// Close the app-server's stdin, so it closes every channel and exits,
    /// then wait for it within the shutdown grace.
    pub async fn shutdown(&self) {
        self.slots.close_child();
        let child = self.child.lock().unwrap().take();
        if let Some(child) = child {
            child.wait_with_grace().await;
        }
    }
}

/// Two glibc malloc arenas, unless the user chose: with the default, a
/// long-lived app-server's memory grows about 45 KiB a run.
fn app_server_env() -> Vec<(&'static str, &'static str)> {
    if std::env::var_os("MALLOC_ARENA_MAX").is_some() {
        return Vec::new();
    }
    vec![("MALLOC_ARENA_MAX", "2")]
}

impl ChannelGuard {
    /// A handle that closes the channel while its guard is held elsewhere.
    pub fn closer(&self) -> ChannelCloser {
        ChannelCloser {
            slots: self.slots.clone(),
            id: self.id,
        }
    }
}

/// Closes a channel whose guard a task still holds, as a cancelled run's.
pub struct ChannelCloser {
    slots: Arc<Slots>,
    id: u64,
}

impl ChannelCloser {
    pub fn close(&self) {
        self.slots.close(self.id);
    }
}

impl Drop for ChannelGuard {
    fn drop(&mut self) {
        self.slots.close(self.id);
    }
}

/// Route each child line to its channel; the child's end ends every channel.
async fn demux(stdout: tokio::process::ChildStdout, slots: Arc<Slots>) {
    let mut lines = LineReader::new(stdout, MAX_LINE);
    while let Some(line) = lines.next().await {
        match line {
            Line::Frame(frame) => slots.route(&frame),
            Line::Oversized(prefix) => slots.fail(channel_of(&prefix)),
        }
    }
    slots.end();
}
