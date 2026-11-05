//! Replay-server drain tracking, so the e2e fence ack never beats owed frames.

use std::sync::atomic::Ordering;

use super::Client;

impl Client {
    /// Record the replay server's `replay/drained` sentinel for request `id`.
    pub fn mark_replay_drained(&self, id: u64) {
        self.replay_drained.fetch_max(id, Ordering::Relaxed);
    }

    /// The app-server's own pid, as `initialize` reported it; `None` when the
    /// server did not send one (a pre-contract or replay server).
    pub fn server_pid(&self) -> Option<u32> {
        match self.server_pid.load(Ordering::Relaxed) {
            0 => None,
            pid => Some(pid),
        }
    }

    /// Record the app-server's pid from its `initialize` response.
    pub fn set_server_pid(&self, pid: u32) {
        self.server_pid.store(pid, Ordering::Relaxed);
    }

    /// Whether a request this client sent still has frames on the way (replay only).
    pub fn owes_replay_frames(&self) -> bool {
        self.replay_drained.load(Ordering::Relaxed) + 1 < self.next_id.load(Ordering::Relaxed)
    }
}
