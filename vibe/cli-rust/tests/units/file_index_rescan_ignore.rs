//! A rescan notice refreshes the ignore matcher alongside the index rebuild.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::RwLock;
use std::time::{SystemTime, UNIX_EPOCH};

use notify::event::{CreateKind, EventKind, Flag};
use notify::Event;
use tokio::sync::watch;

use vibe_rs::utils::file_index::{apply_events, ignore_matcher};

fn temp_root() -> PathBuf {
    static NONCE: AtomicU64 = AtomicU64::new(0);
    let counter = NONCE.fetch_add(1, Ordering::Relaxed);
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!(
        "vibe-rs-file-index-rescan-{}-{nonce}-{counter}",
        std::process::id()
    ));
    fs::create_dir_all(&root).unwrap();
    root
}

fn cleanup(root: &Path) {
    for _ in 0..3 {
        if fs::remove_dir_all(root).is_ok() {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
}

fn ev(kind: EventKind, paths: &[PathBuf]) -> Event {
    let mut event = Event::new(kind);
    event.paths = paths.to_vec();
    event
}

#[test]
fn rescan_refreshes_a_matcher_that_missed_an_ignore_edit() {
    let root = temp_root();
    let paths = RwLock::new(BTreeMap::new());
    let (version_tx, _version_rx) = watch::channel(0u64);
    // The matcher predates the ignore rule and has already matched once, so
    // the root's ignore files are loaded and cached without the rule.
    fs::write(root.join("visible.txt"), "v").unwrap();
    let mut ignore = ignore_matcher(&root);
    let create_visible = ev(
        EventKind::Create(CreateKind::Any),
        &[root.join("visible.txt")],
    );
    apply_events(
        &root,
        &paths,
        vec![create_visible],
        &version_tx,
        &mut ignore,
        false,
    );
    // The .ignore edit's own event is lost with the dropped events: the
    // rescan notice is all that arrives.
    fs::write(root.join(".ignore"), "hidden.log\n").unwrap();
    let rescan = Event::new(EventKind::Other).set_flag(Flag::Rescan);
    apply_events(&root, &paths, vec![rescan], &version_tx, &mut ignore, false);

    // A file matching the missed rule, arriving through a normal incremental
    // event, must stay out: the refreshed matcher rejects it.
    fs::write(root.join("hidden.log"), "h").unwrap();
    let create_hidden = ev(
        EventKind::Create(CreateKind::Any),
        &[root.join("hidden.log")],
    );
    apply_events(
        &root,
        &paths,
        vec![create_hidden],
        &version_tx,
        &mut ignore,
        false,
    );

    let current = paths.read().unwrap();
    assert!(current.contains_key("visible.txt"));
    assert!(current.contains_key(".ignore"));
    assert!(!current.contains_key("hidden.log"));
    cleanup(&root);
}
