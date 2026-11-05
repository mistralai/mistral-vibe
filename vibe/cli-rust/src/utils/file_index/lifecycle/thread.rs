//! The index thread: watcher install, the burst loop and the one-shot build.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, RwLock};
use std::time::Duration;

use notify::{Config as WatcherConfig, RecommendedWatcher, RecursiveMode, Watcher};
use tokio::sync::watch;

use super::super::metrics::{report_batch, report_started};
use super::super::{apply_events_tracked, ignore_matcher, rebuild_index};
use crate::work::Work;

/// How long a drained burst waits for its next event before the thread gets a
/// chance to observe a stop.
const DRAIN_WAIT: Duration = Duration::from_millis(200);
/// How quiet a burst must go before it is applied, and the most it may grow
/// from its first event: one rebuild per settled burst, with bounded delay.
const BURST_QUIET: Duration = Duration::from_millis(150);
const BURST_CAP: Duration = Duration::from_millis(400);

/// `build` keeps the client busy until the initial build is published.
pub(super) fn run_thread(
    root: PathBuf,
    paths: Arc<RwLock<std::collections::BTreeMap<String, bool>>>,
    version: watch::Sender<u64>,
    watcher_on: bool,
    stop: Arc<AtomicBool>,
    build: Work,
) {
    // The watcher reports canonical paths; a symlinked root (macOS `/tmp`)
    // would make every event's strip_prefix fail and drop it.
    let root = root.canonicalize().unwrap_or(root);
    if !watcher_on {
        build_once(&root, &paths, &version, &stop);
        return;
    }
    let (event_tx, event_rx) = mpsc::channel();
    let Ok(mut watcher) = RecommendedWatcher::new(
        move |event| {
            let _ = event_tx.send(event);
        },
        // The index never descends symlinks; following them on inotify can revisit the same dirs forever.
        WatcherConfig::default().with_follow_symlinks(false),
    ) else {
        build_once(&root, &paths, &version, &stop);
        return;
    };
    if watcher.watch(&root, RecursiveMode::Recursive).is_err() {
        // The one-shot build keeps `@` completion correct until the watcher
        // setting changes or the app restarts; a retry in the same session
        // would hit the same exhausted kernel resources.
        build_once(&root, &paths, &version, &stop);
        return;
    }
    let mut git_backed = build_once(&root, &paths, &version, &stop);
    drop(build);
    let mut ignore = ignore_matcher(&root);
    loop {
        // A drain timeout is not the end: it is the window the stop signal is
        // observed in. Only a dropped watcher (or stop) ends the thread.
        if stop.load(Ordering::Relaxed) {
            return;
        }
        let first = match event_rx.recv_timeout(DRAIN_WAIT) {
            Ok(first) => first,
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Err(mpsc::RecvTimeoutError::Disconnected) => return,
        };
        // Busy from the first event: the burst's quiet window must not let an ack beat the update.
        let _batch = Work::start();
        let burst = crate::utils::settle::drain_settled(&event_rx, first, BURST_QUIET, BURST_CAP);
        let events: Vec<_> = burst.into_iter().filter_map(Result::ok).collect();
        if events.is_empty() {
            continue;
        }
        let applied = crate::clock::real_now();
        let (still_git, stats) = apply_events_tracked(
            &root,
            &paths,
            events,
            &version,
            &mut ignore,
            git_backed,
            Some(&stop),
        );
        git_backed = still_git;
        report_batch(
            &stats,
            crate::clock::real_now().duration_since(applied),
            &paths,
        );
    }
}

/// The initial (or watcher-off) full build, published with one version bump.
/// Returns whether the tree is git-backed, so the watch loop keeps applying
/// incrementally in a workspace without a repository. A stop planned before
/// or during the build skips the enumeration's cost and publishes nothing:
/// the handle this thread feeds is being replaced or dropped.
fn build_once(
    root: &Path,
    paths: &Arc<RwLock<std::collections::BTreeMap<String, bool>>>,
    version: &watch::Sender<u64>,
    stop: &AtomicBool,
) -> bool {
    if stop.load(Ordering::Relaxed) {
        return false;
    }
    let started = crate::clock::real_now();
    let (initial, git_backed) = rebuild_index(root, Some(stop));
    if stop.load(Ordering::Relaxed) {
        return git_backed;
    }
    if let Ok(mut current) = paths.write() {
        *current = initial;
    }
    version.send_modify(|value| *value = value.wrapping_add(1));
    report_started(
        git_backed,
        paths,
        crate::clock::real_now().duration_since(started),
    );
    git_backed
}
