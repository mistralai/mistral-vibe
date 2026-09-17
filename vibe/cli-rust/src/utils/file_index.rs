//! Background file index kept fresh by native filesystem notifications.

use std::collections::BTreeMap;
use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{mpsc, Arc, RwLock};

use ignore::{IncrementalIgnore, WalkBuilder};
use notify::{Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use tokio::sync::watch;

use crate::utils::file_match;

#[derive(Clone, Default)]
pub struct FileIndex {
    paths: Arc<RwLock<BTreeMap<String, bool>>>,
}

impl FileIndex {
    pub fn start(root: Option<PathBuf>) -> (Self, watch::Receiver<u64>) {
        let index = Self::default();
        let (version_tx, version_rx) = watch::channel(0);
        let Some(root) = root else {
            return (index, version_rx);
        };
        let paths = index.paths.clone();
        let _ = std::thread::Builder::new()
            .name("file-index-watch".into())
            .spawn(move || watch_files(root, paths, version_tx));
        (index, version_rx)
    }

    pub fn matching(&self, query: &str, limit: usize) -> Vec<String> {
        let Ok(paths) = self.paths.read() else {
            return Vec::new();
        };
        file_match::matching(&paths, query, limit)
    }
}

fn watch_files(
    root: PathBuf,
    paths: Arc<RwLock<BTreeMap<String, bool>>>,
    version: watch::Sender<u64>,
) {
    let (event_tx, event_rx) = mpsc::channel();
    let Ok(mut watcher) = RecommendedWatcher::new(
        move |event| {
            let _ = event_tx.send(event);
        },
        Config::default(),
    ) else {
        return;
    };
    if watcher.watch(&root, RecursiveMode::Recursive).is_err() {
        return;
    }

    let mut ignore = ignore_matcher(&root);
    let (initial, mut git_backed) = rebuild_index(&root);
    if let Ok(mut current) = paths.write() {
        *current = initial;
    }
    bump(&version);

    while let Ok(first) = event_rx.recv() {
        let Ok(first) = first else { continue };
        let mut events = vec![first];
        while let Ok(event) = event_rx.try_recv() {
            if let Ok(event) = event {
                events.push(event);
            }
        }
        git_backed = apply_events(&root, &paths, events, &version, &mut ignore, git_backed);
    }
}

// One git rebuild per drained burst, not one per event (Python defers to a single lazy rebuild).
pub fn apply_events(
    root: &Path,
    paths: &RwLock<BTreeMap<String, bool>>,
    events: Vec<Event>,
    version: &watch::Sender<u64>,
    ignore: &mut IncrementalIgnore,
    git_backed: bool,
) -> bool {
    if git_backed {
        if !events.iter().any(tracks_files) {
            return git_backed;
        }
        let (replacement, still_git) = rebuild_index(root);
        replace_index(paths, replacement, version);
        return still_git;
    }
    for event in events {
        apply_event(root, paths, event, version, ignore);
    }
    git_backed
}

fn apply_event(
    root: &Path,
    paths: &RwLock<BTreeMap<String, bool>>,
    event: Event,
    version: &watch::Sender<u64>,
    ignore: &mut IncrementalIgnore,
) {
    if !tracks_files(&event) {
        return;
    }
    if event.paths.iter().any(|path| is_ignore_file(root, path)) {
        *ignore = ignore_matcher(root);
        replace_index(paths, collect_files(root, root), version);
        return;
    }

    let mut additions = BTreeMap::new();
    let mut removals = Vec::new();
    for path in event.paths {
        let Ok(relative) = path.strip_prefix(root) else {
            continue;
        };
        if path.exists() {
            let is_dir = path.is_dir();
            if is_dir {
                *ignore = ignore_matcher(root);
            }
            if is_git_path(relative) || ignore.matched(relative, is_dir).is_ignore() {
                continue;
            }
            additions.extend(collect_files(&path, root));
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
        let previous_len = current.len();
        current.retain(|entry, _| entry != &relative && !entry.starts_with(&prefix));
        changed |= previous_len != current.len();
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

pub fn rebuild_index(root: &Path) -> (BTreeMap<String, bool>, bool) {
    match git_ls_files(root) {
        Some(entries) => (entries, true),
        None => (collect_files(root, root), false),
    }
}

pub fn git_ls_files(root: &Path) -> Option<BTreeMap<String, bool>> {
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .args([
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ])
        .stderr(Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let mut entries = BTreeMap::new();
    for raw in output.stdout.split(|byte| *byte == 0) {
        if raw.is_empty() {
            continue;
        }
        let rel = String::from_utf8_lossy(raw).into_owned();
        let path = root.join(&rel);
        if !path.exists() {
            continue;
        }
        // Parent dirs exist only as ancestors of a listed file (Python _add_git_entry).
        let parts: Vec<&str> = rel.split('/').collect();
        let mut parent = String::new();
        for part in &parts[..parts.len() - 1] {
            if !parent.is_empty() {
                parent.push('/');
            }
            parent.push_str(part);
            entries.entry(parent.clone()).or_insert(true);
        }
        entries.insert(rel, path.is_dir());
    }
    Some(entries)
}

fn collect_files(scan_root: &Path, workspace_root: &Path) -> BTreeMap<String, bool> {
    walk_builder(scan_root)
        .build()
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let kind = entry.file_type()?;
            let relative = relative_path(workspace_root, entry.path())?;
            (kind.is_file() || kind.is_dir() || kind.is_symlink())
                .then_some((relative, kind.is_dir()))
        })
        .collect()
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
