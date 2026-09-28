//! Git-backed file index parity with Python FileIndexStore.rebuild.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::RwLock;
use std::time::{SystemTime, UNIX_EPOCH};

use notify::event::{AccessKind, CreateKind, EventKind, ModifyKind};
use notify::Event;
use tokio::sync::watch;

use vibe_rs::utils::file_index::{apply_events, git_ls_files, ignore_matcher, rebuild_index};

fn temp_root() -> PathBuf {
    static NONCE: AtomicU64 = AtomicU64::new(0);
    let counter = NONCE.fetch_add(1, Ordering::Relaxed);
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!(
        "vibe-rs-file-index-{}-{nonce}-{counter}",
        std::process::id()
    ));
    fs::create_dir_all(&root).unwrap();
    root
}

/// Best-effort teardown: the assertions already ran, so a transient OS error
/// (a concurrent temp-dir indexer, an APFS directory-removal race) must not
/// fail the test. The system purges $TMPDIR on its own schedule.
fn cleanup(root: &Path) {
    for _ in 0..3 {
        if fs::remove_dir_all(root).is_ok() {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
}

fn git(root: &Path, args: &[&str]) {
    let status = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .status()
        .expect("git binary available");
    assert!(status.success(), "git {args:?} failed: {status}");
}

#[test]
fn git_index_lists_visible_files_and_synthesizes_parent_dirs() {
    let root = temp_root();
    git(&root, &["init", "--quiet"]);
    fs::write(root.join("tracked.txt"), "t").unwrap();
    fs::create_dir(root.join("src")).unwrap();
    fs::write(root.join("src/lib.rs"), "x").unwrap();
    fs::write(root.join("untracked.txt"), "u").unwrap();
    fs::write(root.join(".gitignore"), "ignored.log\ntarget/\n").unwrap();
    fs::write(root.join("ignored.log"), "i").unwrap();
    fs::create_dir_all(root.join("wrapper/target")).unwrap();
    fs::write(root.join("wrapper/target/out.txt"), "o").unwrap();
    git(&root, &["add", "tracked.txt", "src/lib.rs"]);

    let (entries, git_backed) = rebuild_index(&root);

    assert!(git_backed);
    assert_eq!(entries.get("tracked.txt"), Some(&false));
    assert_eq!(entries.get("untracked.txt"), Some(&false));
    assert_eq!(entries.get("src"), Some(&true));
    assert_eq!(entries.get("src/lib.rs"), Some(&false));
    assert_eq!(entries.get(".gitignore"), Some(&false));
    assert_eq!(entries.len(), 5);
    // Excluded by --exclude-standard, and a dir with no visible file disappears.
    assert!(!entries.contains_key("ignored.log"));
    assert!(!entries.contains_key("wrapper"));
    assert!(!entries.contains_key("wrapper/target"));
    cleanup(&root);
}

#[test]
fn git_index_skips_stale_index_entries() {
    let root = temp_root();
    git(&root, &["init", "--quiet"]);
    fs::write(root.join("stale.txt"), "s").unwrap();
    git(&root, &["add", "stale.txt"]);
    fs::remove_file(root.join("stale.txt")).unwrap();

    let entries = git_ls_files(&root).unwrap();

    // Tracked on disk-in-the-index only: the path check drops it (Python path.exists()).
    assert!(entries.is_empty());
    cleanup(&root);
}

#[test]
fn non_git_roots_fall_back_to_the_filesystem_walk() {
    let root = temp_root();
    fs::create_dir_all(root.join("nested/target")).unwrap();
    fs::write(root.join("nested/target/out.txt"), "o").unwrap();
    fs::write(root.join("plain.md"), "p").unwrap();

    let (entries, git_backed) = rebuild_index(&root);

    // Without a repo the walk drives the index and keeps ignored-content dirs.
    assert!(!git_backed);
    assert_eq!(entries.get("plain.md"), Some(&false));
    assert_eq!(entries.get("nested"), Some(&true));
    assert_eq!(entries.get("nested/target"), Some(&true));
    assert_eq!(entries.get("nested/target/out.txt"), Some(&false));
    cleanup(&root);
}

#[test]
fn git_backed_batch_collapses_into_one_rebuild_and_skips_reads() {
    let root = temp_root();
    git(&root, &["init", "--quiet"]);
    fs::write(root.join("tracked.txt"), "t").unwrap();
    git(&root, &["add", "tracked.txt"]);
    let paths = RwLock::new(BTreeMap::new());
    let (version_tx, mut version_rx) = watch::channel(0u64);
    let mut ignore = ignore_matcher(&root);
    version_rx.borrow_and_update();

    // Read events never reach the index.
    let mut read = Event::new(EventKind::Access(AccessKind::Read));
    read.paths.push(root.join("tracked.txt"));
    assert!(apply_events(
        &root,
        &paths,
        vec![read],
        &version_tx,
        &mut ignore,
        true
    ));
    assert!(!version_rx.has_changed().unwrap());

    // A burst collapses into a single rebuild: exactly one version bump for two events.
    fs::write(root.join("fresh.txt"), "f").unwrap();
    let mut create = Event::new(EventKind::Create(CreateKind::Any));
    create.paths.push(root.join("fresh.txt"));
    let mut modify = Event::new(EventKind::Modify(ModifyKind::Any));
    modify.paths.push(root.join("tracked.txt"));
    assert!(apply_events(
        &root,
        &paths,
        vec![create, modify],
        &version_tx,
        &mut ignore,
        true
    ));
    let current = paths.read().unwrap();
    assert!(current.contains_key("tracked.txt"));
    assert!(current.contains_key("fresh.txt"));
    drop(current);
    assert_eq!(*version_rx.borrow_and_update(), 1);
    cleanup(&root);
}

#[test]
fn non_git_batch_keeps_incremental_event_application() {
    let root = temp_root();
    fs::write(root.join("plain.md"), "p").unwrap();
    let paths = RwLock::new(BTreeMap::new());
    let (version_tx, _version_rx) = watch::channel(0u64);
    let mut ignore = ignore_matcher(&root);

    let mut create = Event::new(EventKind::Create(CreateKind::Any));
    create.paths.push(root.join("plain.md"));
    assert!(!apply_events(
        &root,
        &paths,
        vec![create],
        &version_tx,
        &mut ignore,
        false
    ));

    let current = paths.read().unwrap();
    assert_eq!(current.get("plain.md"), Some(&false));
    cleanup(&root);
}
