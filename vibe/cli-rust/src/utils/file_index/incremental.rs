//! Incremental index updates share the batch's minimal directory scan plan.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::atomic::AtomicBool;
use std::sync::RwLock;

use ignore::IncrementalIgnore;
use notify::{event::ModifyKind, Event, EventKind};
use tokio::sync::watch;

use super::directory_scans::DirectoryScans;
use super::{bump, collect_files, display_path, is_git_path, stopped, tracks_files, BatchStats};

pub(super) struct Updates<'a> {
    pub root: &'a Path,
    pub paths: &'a RwLock<BTreeMap<String, bool>>,
    pub version: &'a watch::Sender<u64>,
}

impl Updates<'_> {
    pub fn apply(
        &self,
        event: Event,
        ignore: &mut IncrementalIgnore,
        stats: &mut BatchStats,
        stop: Option<&AtomicBool>,
        scans: &mut DirectoryScans,
    ) {
        let (root, paths, version) = (self.root, self.paths, self.version);
        if stopped(stop) {
            return;
        }
        if !tracks_files(&event) {
            return;
        }
        let (mut additions, mut removals, kind) = (BTreeMap::new(), Vec::new(), event.kind);
        for path in event.paths {
            let Some(relative) = path.strip_prefix(root).ok().filter(|r| !is_git_path(r)) else {
                continue;
            };
            if let Ok(metadata) = path.metadata() {
                let is_dir = metadata.is_dir();
                if ignore.matched(relative, is_dir).is_ignore() {
                    continue;
                }
                if is_dir {
                    // A Modify on a directory means a child changed; children arrive per-path.
                    let created = matches!(kind, EventKind::Create(_));
                    let renamed = matches!(kind, EventKind::Modify(ModifyKind::Name(_)));
                    if created || renamed {
                        let Some(scan_root) = scans.take(&path) else {
                            continue;
                        };
                        let found = collect_files(&scan_root, root, stop);
                        if stopped(stop) {
                            return;
                        }
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
}
