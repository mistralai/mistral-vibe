//! File-index watch metrics, logged as RUM-style resource records.

use std::collections::BTreeMap;
use std::sync::RwLock;
use std::time::Duration;

/// What one drained event burst cost: count, size and status, never a path.
#[derive(Default)]
pub struct BatchStats {
    pub events: u64,
    pub walks: u64,
    pub walked_entries: u64,
    pub rebuilt: bool,
}

impl BatchStats {
    pub fn new(events: u64) -> Self {
        Self {
            events,
            ..Default::default()
        }
    }

    pub fn walk(&mut self, found: &BTreeMap<String, bool>) {
        self.walks += 1;
        self.walked_entries += found.len() as u64;
    }

    /// A rebuild enumerates the whole index once, via git ls-files or a walk.
    pub fn rebuild(&mut self, found: &BTreeMap<String, bool>) {
        self.rebuilt = true;
        self.walk(found);
    }
}

fn entries(paths: &RwLock<BTreeMap<String, bool>>) -> u64 {
    paths.read().map(|paths| paths.len() as u64).unwrap_or(0)
}

/// Once per session, after the initial index build.
///
/// Logged on the watch thread, not shipped as telemetry: riding the event
/// loop's channel would gate the replay harness's idle marker on a queue
/// that only a redraw drains.
pub(super) fn report_started(
    git_backed: bool,
    paths: &RwLock<BTreeMap<String, bool>>,
    build: Duration,
) {
    tracing::debug!(
        name = "vibe.file_index.started",
        is_git_backed = git_backed,
        entries = entries(paths),
        duration_ms = build.as_millis() as u64,
        "file index built"
    );
}

/// One record per drained burst: event count, apply cost, entries enumerated.
pub(super) fn report_batch(
    stats: &BatchStats,
    apply: Duration,
    paths: &RwLock<BTreeMap<String, bool>>,
) {
    tracing::debug!(
        name = "vibe.file_index.applied",
        events = stats.events,
        apply_ms = apply.as_millis() as u64,
        walks = stats.walks,
        walked_entries = stats.walked_entries,
        has_rebuilt = stats.rebuilt,
        entries = entries(paths),
        "file index burst applied"
    );
}
