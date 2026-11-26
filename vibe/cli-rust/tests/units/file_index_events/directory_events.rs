//! Directory scan planning preserves entries, renames and bounded overflow.

use std::fs;

use notify::event::{CreateKind, ModifyKind, RenameMode};
use notify::EventKind;

use super::Workspace;

const CREATED: EventKind = EventKind::Create(CreateKind::Folder);

#[test]
fn overlapping_directory_creates_walk_once_in_either_event_order() {
    for parent_first in [false, true] {
        let mut ws = Workspace::new(&[]);
        ws.write("new/child/grandchild/file.txt", "v");
        let mut events = vec![
            ws.event(CREATED, "new/child/grandchild"),
            ws.event(CREATED, "new/child"),
            ws.event(CREATED, "new"),
        ];
        if parent_first {
            events.reverse();
        }
        let stats = ws.apply(events);
        assert_eq!(stats.walks, 1);
        assert_eq!(stats.walked_entries, 4);
        assert!(ws.has("new/child/grandchild/file.txt"));
        ws.assert_matches_walk();
    }
}

#[test]
fn independent_directories_are_both_scanned() {
    let mut ws = Workspace::new(&[]);
    ws.write("first/a.txt", "a");
    ws.write("second/b.txt", "b");
    let stats = ws.apply(vec![
        ws.event(CREATED, "first"),
        ws.event(CREATED, "second"),
    ]);
    assert_eq!(stats.walks, 2);
    ws.assert_matches_walk();
}

#[test]
fn rename_removes_old_subtree_and_scans_the_new_parent_once() {
    let mut ws = Workspace::new(&[("old/child/file.txt", "v")]);
    fs::rename(
        ws.directory.path().join("old"),
        ws.directory.path().join("new"),
    )
    .unwrap();
    let renamed = ws
        .event(EventKind::Modify(ModifyKind::Name(RenameMode::Both)), "old")
        .add_path(ws.directory.path().join("new"));
    let stats = ws.apply(vec![ws.event(CREATED, "new/child"), renamed]);
    assert_eq!(stats.walks, 1);
    assert!(!ws.has("old/child/file.txt"));
    assert!(ws.has("new/child/file.txt"));
    ws.assert_matches_walk();
}

#[test]
fn ignored_directory_creates_never_walk() {
    let mut ws = Workspace::new(&[(".ignore", "ignored/\n")]);
    ws.write("ignored/child/file.txt", "v");
    let stats = ws.apply(vec![
        ws.event(CREATED, "ignored/child"),
        ws.event(CREATED, "ignored"),
    ]);
    assert_eq!(stats.walks, 0);
    assert_eq!(*ws.updates.borrow(), 0);
    ws.assert_matches_walk();
}

#[test]
fn directory_scan_capacity_overflow_keeps_every_directory() {
    let mut ws = Workspace::new(&[]);
    let mut events = Vec::new();
    for i in 0..140 {
        let directory = format!("dir{i:03}");
        ws.write(&format!("{directory}/file.txt"), "v");
        events.push(ws.event(CREATED, &directory));
    }
    let stats = ws.apply(events);
    assert_eq!(stats.walks, 140);
    assert!(!stats.rebuilt);
    ws.assert_matches_walk();
}

#[test]
fn file_content_writes_neither_walk_nor_publish() {
    let mut ws = Workspace::new(&[("visible.txt", "v")]);
    ws.write("visible.txt", "new");
    let stats = ws.apply(vec![
        ws.event(EventKind::Modify(ModifyKind::Any), "visible.txt")
    ]);
    assert_eq!(stats.walks, 0);
    assert_eq!(*ws.updates.borrow(), 0);
    ws.assert_matches_walk();
}
