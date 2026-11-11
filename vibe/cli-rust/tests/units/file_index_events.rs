//! File-index batches skip excluded rules and collapse redundant directory walks.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::sync::atomic::AtomicBool;
use std::sync::RwLock;

use ignore::IncrementalIgnore;
use notify::{Event, EventKind};
use tempfile::TempDir;
use tokio::sync::watch;
use vibe_rs::utils::file_index::{apply_events_tracked, ignore_matcher, rebuild_index, BatchStats};

#[path = "file_index_events/directory_events.rs"]
mod directory_events;
#[path = "file_index_events/ignore_files.rs"]
mod ignore_files;

struct Workspace {
    directory: TempDir,
    paths: RwLock<BTreeMap<String, bool>>,
    ignore: IncrementalIgnore,
    version: watch::Sender<u64>,
    updates: watch::Receiver<u64>,
}

impl Workspace {
    fn new(files: &[(&str, &str)]) -> Self {
        let directory = tempfile::tempdir().unwrap();
        for (path, content) in files {
            write(directory.path(), path, content);
        }
        let (paths, git_backed) = rebuild_index(directory.path(), None);
        assert!(!git_backed);
        let mut ignore = ignore_matcher(directory.path());
        for (path, _) in files {
            ignore.matched(Path::new(path), false);
        }
        let (version, updates) = watch::channel(0);
        Self {
            directory,
            paths: RwLock::new(paths),
            ignore,
            version,
            updates,
        }
    }

    fn event(&self, kind: EventKind, path: &str) -> Event {
        Event::new(kind).add_path(self.directory.path().join(path))
    }

    fn write(&self, path: &str, content: &str) {
        write(self.directory.path(), path, content);
    }

    fn apply(&mut self, events: Vec<Event>) -> BatchStats {
        self.apply_stoppable(events, None)
    }

    fn apply_stoppable(&mut self, events: Vec<Event>, stop: Option<&AtomicBool>) -> BatchStats {
        let (_, stats) = apply_events_tracked(
            self.directory.path(),
            &self.paths,
            events,
            &self.version,
            &mut self.ignore,
            false,
            stop,
        );
        stats
    }

    fn has(&self, path: &str) -> bool {
        self.paths.read().unwrap().contains_key(path)
    }

    fn assert_matches_walk(&self) {
        let (expected, _) = rebuild_index(self.directory.path(), None);
        assert_eq!(*self.paths.read().unwrap(), expected);
    }
}

fn write(root: &Path, path: &str, content: &str) {
    let path = root.join(path);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, content).unwrap();
}
