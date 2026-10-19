//! Replay-server drain tracking, so the e2e idle marker never beats owed frames.

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

    /// A detached telemetry task will send a frame: owed until `telemetry_written`.
    pub fn telemetry_spawned(&self) {
        self.unsent_telemetry.fetch_add(1, Ordering::Relaxed);
    }

    /// The telemetry frame got its request id, so `next_id` now tracks it.
    pub fn telemetry_written(&self) {
        self.unsent_telemetry.fetch_sub(1, Ordering::Release);
    }

    /// Whether a request this client sent still has frames on the way (replay only).
    pub fn owes_replay_frames(&self) -> bool {
        self.unsent_telemetry.load(Ordering::Acquire) > 0
            || self.replay_drained.load(Ordering::Relaxed) + 1
                < self.next_id.load(Ordering::Relaxed)
    }
}
