//! File-index watch metrics: burst stats and the pre-session telemetry hold.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::RwLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use notify::event::{CreateKind, EventKind, Flag};
use notify::Event;
use tokio::sync::{mpsc, watch};

use vibe_rs::app::App;
use vibe_rs::telemetry::{event, TelemetryEvent};
use vibe_rs::utils::file_index::{apply_events_tracked, ignore_matcher};

fn temp_root() -> PathBuf {
    static NONCE: AtomicU64 = AtomicU64::new(0);
    let counter = NONCE.fetch_add(1, Ordering::Relaxed);
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!(
        "vibe-rs-file-index-metrics-{}-{nonce}-{counter}",
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
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn ev(kind: EventKind, paths: &[PathBuf]) -> Event {
    let mut event = Event::new(kind);
    event.paths = paths.to_vec();
    event
}

#[test]
fn tracked_apply_counts_walks_only_for_directory_creates() {
    let root = temp_root();
    fs::create_dir_all(root.join("sub")).unwrap();
    fs::write(root.join("sub/a.txt"), "a").unwrap();
    let paths = RwLock::new(BTreeMap::new());
    let (version_tx, _version_rx) = watch::channel(0u64);
    let mut ignore = ignore_matcher(&root);

    // A file event upserts without walking anything.
    let file = root.join("plain.md");
    fs::write(&file, "p").unwrap();
    let (git_backed, stats) = apply_events_tracked(
        &root,
        &paths,
        vec![ev(EventKind::Create(CreateKind::Any), &[file])],
        &version_tx,
        &mut ignore,
        false,
    );
    assert!(!git_backed);
    assert_eq!(stats.events, 1);
    assert_eq!(stats.walks, 0);
    assert_eq!(stats.walked_entries, 0);
    assert!(!stats.rebuilt);

    // A directory create walks its subtree once and reports what it saw.
    let (_, stats) = apply_events_tracked(
        &root,
        &paths,
        vec![ev(EventKind::Create(CreateKind::Any), &[root.join("sub")])],
        &version_tx,
        &mut ignore,
        false,
    );
    assert_eq!(stats.walks, 1);
    assert_eq!(stats.walked_entries, 2);
    assert!(!stats.rebuilt);
    assert!(paths.read().unwrap().contains_key("sub/a.txt"));
    cleanup(&root);
}

#[test]
fn tracked_apply_marks_rescans_as_full_rebuilds() {
    let root = temp_root();
    let paths = RwLock::new(BTreeMap::new());
    let (version_tx, _version_rx) = watch::channel(0u64);
    let mut ignore = ignore_matcher(&root);

    fs::write(root.join("unseen.txt"), "u").unwrap();
    let rescan = Event::new(EventKind::Other).set_flag(Flag::Rescan);
    let (git_backed, stats) =
        apply_events_tracked(&root, &paths, vec![rescan], &version_tx, &mut ignore, false);
    assert!(!git_backed);
    assert!(stats.rebuilt);
    assert_eq!(stats.walks, 1);
    assert!(paths.read().unwrap().contains_key("unseen.txt"));
    cleanup(&root);
}

#[test]
fn pre_session_events_are_held_until_the_session_starts() {
    let mut app = App::default();
    let (tx, mut rx) = mpsc::channel(8);
    app.telemetry_tx = Some(tx);
    // An emitter can queue before the handshake lands its id.
    app.session.held_telemetry.push(TelemetryEvent {
        name: event::AT_MENTION_INSERTED,
        properties: BTreeMap::new(),
    });
    assert!(
        rx.try_recv().is_err(),
        "a held event must not be sent early"
    );

    app.set_session_id("session-1".into());

    let flushed = rx.try_recv().expect("held event flushed on session start");
    assert_eq!(flushed.name, event::AT_MENTION_INSERTED);
    assert!(rx.try_recv().is_err(), "the held queue drains once");
}
