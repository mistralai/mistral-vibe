//! Lifecycle of the background index thread: start, stop, watcher-off builds.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, RwLock};

use tokio::sync::watch;

use super::FileIndex;

mod thread;

/// Whether `VIBE_ENABLE_FILE_AUTOCOMPLETE` names a false value; the server
/// layer parses it too, this only avoids a launch-time index build. The list
/// matches pydantic's false values exactly ('n' and 'f' included), so a
/// falsy shortcut here never disagrees with the server's verdict.
pub fn env_opt_out() -> bool {
    std::env::var("VIBE_ENABLE_FILE_AUTOCOMPLETE").is_ok_and(|value| {
        matches!(
            value.to_ascii_lowercase().as_str(),
            "0" | "false" | "no" | "off" | "n" | "f"
        )
    })
}

/// What the reducer asks the loop to do with the file index.
#[derive(Clone, PartialEq)]
pub enum FileIndexAction {
    /// Build (and watch) the index for a root.
    Start { root: PathBuf, watcher: bool },
    /// End the thread; no `@` completion until a later start.
    Stop,
}

impl FileIndexAction {
    /// Compare the new startup config against the running handle. `Start`
    /// also covers a restart (a watcher-flag change rebuilds in place) and a
    /// deferred start (the parked root is the worktree settle when there was
    /// one); the session cwd backs a later re-enable.
    pub fn plan(
        handle: &FileIndexHandle,
        next: &crate::utils::startup_cache::StartupConfig,
        session_cwd: Option<&str>,
    ) -> Option<Self> {
        let enabled = next.enable_file_autocomplete;
        let watcher = next.file_watcher_for_autocomplete;
        if handle.running() {
            if !enabled {
                return Some(Self::Stop);
            }
            // A failed watcher install left a one-shot build behind; it stays
            // until this watcher flag changes (or the app restarts), since a
            // same-session retry hits the same exhausted kernel resources.
            if handle.watcher == watcher {
                return None;
            }
            return handle
                .root
                .clone()
                .or_else(|| handle.parked_root.clone())
                .or_else(|| session_cwd.map(PathBuf::from))
                .map(|root| Self::Start { root, watcher });
        }
        if !enabled {
            return None;
        }
        let root = handle
            .parked_root
            .clone()
            .or_else(|| session_cwd.map(PathBuf::from));
        root.map(|root| Self::Start { root, watcher })
    }
}

/// The UI thread's handle: the shared index, the version feed and the stop
/// signal. Setting the flag ends the thread (it owns its own copy), so
/// `App::completion.files` owns the index's lifetime.
pub struct FileIndexHandle {
    pub index: FileIndex,
    pub changes: watch::Receiver<u64>,
    /// Where a not-yet-started index should build, once a start lands.
    pub parked_root: Option<PathBuf>,
    stop: Option<Arc<AtomicBool>>,
    watcher: bool,
    /// The tree a running thread indexes; a restart keeps it over a session
    /// cwd that may still name the pre-settle checkout.
    root: Option<PathBuf>,
    /// The last version the UI applied; a handle outlives its thread, so the
    /// final bump must count after the feed's sender is dropped.
    seen: AtomicU64,
}

impl Default for FileIndexHandle {
    fn default() -> Self {
        let (version_tx, changes) = watch::channel(0u64);
        // The sender's only job is keeping the feed alive for a handle that
        // has nothing to report yet.
        std::mem::forget(version_tx);
        Self {
            index: FileIndex::from_paths(Arc::new(RwLock::new(std::collections::BTreeMap::new()))),
            changes,
            parked_root: None,
            stop: None,
            watcher: false,
            root: None,
            seen: AtomicU64::new(0),
        }
    }
}

impl FileIndexHandle {
    /// Start the thread for `root`; `watcher=false` builds the index once and
    /// exits without installing a filesystem watcher.
    pub fn start(root: PathBuf, watcher: bool) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let paths = Arc::new(RwLock::new(std::collections::BTreeMap::new()));
        let (version_tx, changes) = watch::channel(0u64);
        let thread_root = root.clone();
        let build = crate::work::Work::start();
        let spawned = std::thread::Builder::new()
            .name("file-index-watch".into())
            .spawn({
                let paths = paths.clone();
                let stop = stop.clone();
                move || thread::run_thread(thread_root, paths, version_tx, watcher, stop, build)
            });
        // A failed spawn leaves a parked handle: an empty index matches
        // nothing, and the next config apply boots a replacement thread
        // instead of one that claims to be running forever.
        if spawned.is_err() {
            return Self {
                parked_root: Some(root),
                ..Self::default()
            };
        }
        Self {
            index: FileIndex::from_paths(paths),
            changes,
            parked_root: None,
            stop: Some(stop),
            watcher,
            root: Some(root),
            seen: AtomicU64::new(0),
        }
    }
    /// A handle holding the root to build once a start lands, with no thread.
    pub fn parked(root: PathBuf) -> Self {
        Self {
            parked_root: Some(root),
            ..Self::default()
        }
    }

    /// Remember where a not-yet-started index should build.
    pub fn park(&mut self, root: PathBuf) {
        self.parked_root = Some(root);
    }

    /// Ask the thread to end; it exits within one drain window.
    pub fn stop(&self) {
        if let Some(stop) = &self.stop {
            stop.store(true, Ordering::Relaxed);
        }
    }

    /// True while a published version has not been applied by the UI yet.
    pub fn has_update(&self) -> bool {
        *self.changes.borrow() != self.seen.load(Ordering::Relaxed)
    }

    /// True while the thread is expected to keep publishing versions.
    pub fn watching(&self) -> bool {
        self.stop
            .as_ref()
            .is_none_or(|stop| !stop.load(Ordering::Relaxed))
    }

    /// True when a thread is running and has not been asked to stop.
    pub fn running(&self) -> bool {
        self.stop.is_some() && self.watching()
    }

    /// Consume one not-yet-applied version bump. `has_changed` turns Err the
    /// moment the thread exits and drops the feed's sender, so the version is
    /// compared against the last one the UI applied instead.
    pub fn take_update(&mut self) -> bool {
        let current = *self.changes.borrow();
        if current == self.seen.load(Ordering::Relaxed) {
            return false;
        }
        self.seen.store(current, Ordering::Relaxed);
        self.changes.borrow_and_update();
        true
    }
}

/// Apply a start/stop decision to the handle. Runs on the event loop thread.
pub fn apply(handle: &mut FileIndexHandle, action: &FileIndexAction) {
    match action {
        FileIndexAction::Start { root, watcher } => {
            handle.stop();
            *handle = FileIndexHandle::start(root.clone(), *watcher);
        }
        FileIndexAction::Stop => {
            handle.stop();
            handle.stop = None;
            // The tree survives the opt-out: a later re-enable rebuilds at the
            // tree this handle knew, not a session cwd that may still name
            // the pre-settle checkout.
            handle.parked_root = handle.root.take();
            // The opt-out exists to drop the index's memory; an emptied map
            // also matches nothing if a stale `@` popup ever renders.
            handle.index = FileIndex::default();
        }
    }
}

/// Plan and apply the start/stop decision for `config` on the spot. Applying
/// where the plan is made keeps two nearby config updates (a panel-close
/// response and its notification) from both deciding on the same stale
/// handle, which no queue can prevent.
pub fn reconcile(
    handle: &mut FileIndexHandle,
    next: &crate::utils::startup_cache::StartupConfig,
    session_cwd: Option<&str>,
) {
    if let Some(action) = FileIndexAction::plan(handle, next, session_cwd) {
        apply(handle, &action);
    }
}
