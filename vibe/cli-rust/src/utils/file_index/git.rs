//! Git-backed index rebuilds.

use std::collections::BTreeMap;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};

use super::collect_files;

/// `None` when nothing can stop this enumeration; a stopped one returns a
/// partial map its caller must not publish.
pub fn rebuild_index(root: &Path, stop: Option<&AtomicBool>) -> (BTreeMap<String, bool>, bool) {
    match git_ls_files(root, stop) {
        Some(entries) => (entries, true),
        None => (collect_files(root, root, stop), false),
    }
}

pub fn git_ls_files(root: &Path, stop: Option<&AtomicBool>) -> Option<BTreeMap<String, bool>> {
    if stop.is_some_and(|stop| stop.load(Ordering::Relaxed)) {
        return None;
    }
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        // A repo's fsmonitor can run its own command or hang on a wedged daemon.
        .args(["-c", "core.fsmonitor="])
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
        if stop.is_some_and(|stop| stop.load(Ordering::Relaxed)) {
            return Some(entries);
        }
        let rel = String::from_utf8_lossy(raw).into_owned();
        let path = root.join(&rel);
        if !path.exists() {
            continue;
        }
        // Parent dirs exist only as ancestors of a listed file.
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
