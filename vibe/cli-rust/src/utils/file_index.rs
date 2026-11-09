//! Background file index kept fresh by native filesystem notifications.

use std::collections::BTreeMap;
use std::ffi::OsStr;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, RwLock};

use ignore::{IncrementalIgnore, WalkBuilder};
use notify::{event::ModifyKind, Event, EventKind};
use tokio::sync::watch;

use crate::utils::file_match;

mod git;
mod lifecycle;
mod metrics;

pub use git::{git_ls_files, rebuild_index};
pub use lifecycle::{apply, env_opt_out, reconcile, FileIndexAction, FileIndexHandle};
pub use metrics::BatchStats;

#[derive(Clone, Default)]
pub struct FileIndex {
    paths: Arc<RwLock<BTreeMap<String, bool>>>,
}

impl FileIndex {
    pub(crate) fn from_paths(paths: Arc<RwLock<BTreeMap<String, bool>>>) -> Self {
        Self { paths }
    }

    pub fn matching(&self, query: &str, limit: usize) -> Vec<String> {
        let Ok(paths) = self.paths.read() else {
            return Vec::new();
        };
        file_match::matching(&paths, query, limit)
    }
}

// One full rebuild per drained burst, not one per event.
pub fn apply_events(
    root: &Path,
    paths: &RwLock<BTreeMap<String, bool>>,
    events: Vec<Event>,
    version: &watch::Sender<u64>,
    ignore: &mut IncrementalIgnore,
    git_backed: bool,
) -> bool {
    apply_events_tracked(root, paths, events, version, ignore, git_backed, None).0
}

// `apply_events` plus what the burst cost, for the watch thread's telemetry.
// `stop` lets a rebuild planned mid-burst bail instead of finishing into a
// handle nobody reads.
pub fn apply_events_tracked(
    root: &Path,
    paths: &RwLock<BTreeMap<String, bool>>,
    events: Vec<Event>,
    version: &watch::Sender<u64>,
    ignore: &mut IncrementalIgnore,
    git_backed: bool,
    stop: Option<&AtomicBool>,
) -> (bool, BatchStats) {
    let mut stats = BatchStats::new(events.len() as u64);
    if stopped(stop) {
        return (git_backed, stats);
    }
    let rescan = events.iter().any(Event::need_rescan);
    if git_backed || rescan {
        if !rescan && !events.iter().any(tracks_files) {
            return (git_backed, stats);
        }
        // A rescan means events were dropped; an ignore-file edit may have
        // been lost with them, so the matcher needs a rebuild too.
        if rescan {
            *ignore = ignore_matcher(root);
        }
        let (replacement, still_git) = rebuild_index(root, stop);
        if stopped(stop) {
            return (still_git, stats);
        }
        stats.rebuild(&replacement);
        replace_index(paths, replacement, version);
        return (still_git, stats);
    }
    for event in events {
        apply_event(root, paths, event, version, ignore, &mut stats, stop);
    }
    (git_backed, stats)
}

fn stopped(stop: Option<&AtomicBool>) -> bool {
    stop.is_some_and(|stop| stop.load(Ordering::Relaxed))
}

fn apply_event(
    root: &Path,
    paths: &RwLock<BTreeMap<String, bool>>,
    event: Event,
    version: &watch::Sender<u64>,
    ignore: &mut IncrementalIgnore,
    stats: &mut BatchStats,
    stop: Option<&AtomicBool>,
) {
    if stopped(stop) {
        return;
    }
    if !tracks_files(&event) {
        return;
    }
    if event.paths.iter().any(|path| is_ignore_file(root, path)) {
        *ignore = ignore_matcher(root);
        let replacement = collect_files(root, root, stop);
        stats.rebuild(&replacement);
        replace_index(paths, replacement, version);
        return;
    }

    let (mut additions, mut removals, kind) = (BTreeMap::new(), Vec::new(), event.kind);
    for path in event.paths {
        let Some(relative) = path.strip_prefix(root).ok().filter(|r| !is_git_path(r)) else {
            continue;
        };
        if path.exists() {
            let is_dir = path.is_dir();
            if ignore.matched(relative, is_dir).is_ignore() {
                continue;
            }
            if is_dir {
                // A Modify on a directory means a child changed; children arrive per-path.
                let created = matches!(kind, EventKind::Create(_));
                let renamed = matches!(kind, EventKind::Modify(ModifyKind::Name(_)));
                if created || renamed {
                    let found = collect_files(&path, root, stop);
                    stats.walk(&found);
                    additions.extend(found);
                }
            } else if let Some(relative) = display_path(relative) {
                additions.insert(relative, false);
            }
        } else if let Some(relative) = display_path(relative) {
            removals.push(relative);
        }
    }
    if additions.is_empty() && removals.is_empty() {
        return;
    }
    let Ok(mut current) = paths.write() else {
        return;
    };
    let mut changed = false;
    for relative in removals {
        let prefix = format!("{relative}/");
        let stale: Vec<String> = current
            .range(prefix.clone()..)
            .take_while(|(key, _)| key.starts_with(&prefix))
            .map(|(key, _)| key.clone())
            .collect();
        for key in stale.iter().chain([&relative]) {
            changed |= current.remove(key).is_some();
        }
    }
    for (path, is_dir) in additions {
        changed |= current.insert(path, is_dir) != Some(is_dir);
    }
    drop(current);
    if changed {
        bump(version);
    }
}

fn tracks_files(event: &Event) -> bool {
    matches!(
        event.kind,
        EventKind::Create(_) | EventKind::Remove(_) | EventKind::Modify(_)
    )
}

fn replace_index(
    paths: &RwLock<BTreeMap<String, bool>>,
    replacement: BTreeMap<String, bool>,
    version: &watch::Sender<u64>,
) {
    if let Ok(mut current) = paths.write() {
        *current = replacement;
        drop(current);
        bump(version);
    }
}

/// `stop` aborts the walk and returns what it has; `None` enumerates it all.
fn collect_files(
    scan_root: &Path,
    workspace_root: &Path,
    stop: Option<&AtomicBool>,
) -> BTreeMap<String, bool> {
    let mut entries = BTreeMap::new();
    for entry in walk_builder(scan_root).build().flatten() {
        if stop.is_some_and(|stop| stop.load(Ordering::Relaxed)) {
            return entries;
        }
        let Some(kind) = entry.file_type() else {
            continue;
        };
        let Some(relative) = relative_path(workspace_root, entry.path()) else {
            continue;
        };
        if kind.is_file() || kind.is_dir() || kind.is_symlink() {
            entries.insert(relative, kind.is_dir());
        }
    }
    entries
}

fn walk_builder(root: &Path) -> WalkBuilder {
    let mut builder = WalkBuilder::new(root);
    builder
        .hidden(false)
        .parents(true)
        .ignore(true)
        .git_ignore(true)
        .git_global(true)
        .git_exclude(true)
        .filter_entry(|entry| entry.file_name() != OsStr::new(".git"));
    builder
}

pub fn ignore_matcher(root: &Path) -> IncrementalIgnore {
    walk_builder(root)
        .build_matchers()
        .into_iter()
        .next()
        .expect("a walk builder always has its root matcher")
}

fn relative_path(root: &Path, path: &Path) -> Option<String> {
    let relative = path.strip_prefix(root).ok()?;
    display_path(relative)
}

fn display_path(path: &Path) -> Option<String> {
    let value = path.to_string_lossy().replace('\\', "/");
    (!value.is_empty()).then_some(value)
}

fn is_git_path(path: &Path) -> bool {
    path.components()
        .any(|component| component.as_os_str() == OsStr::new(".git"))
}

fn is_ignore_file(root: &Path, path: &Path) -> bool {
    let Ok(relative) = path.strip_prefix(root) else {
        return false;
    };
    matches!(relative.file_name(), Some(name) if name == OsStr::new(".gitignore") || name == OsStr::new(".ignore"))
        || relative.ends_with(Path::new(".git").join("info").join("exclude"))
}

fn bump(version: &watch::Sender<u64>) {
    version.send_modify(|value| *value = value.wrapping_add(1));
}
