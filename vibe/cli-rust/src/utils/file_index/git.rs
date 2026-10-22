//! Git-backed index rebuilds.

use std::collections::BTreeMap;
use std::path::Path;
use std::process::{Command, Stdio};

use super::collect_files;

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
