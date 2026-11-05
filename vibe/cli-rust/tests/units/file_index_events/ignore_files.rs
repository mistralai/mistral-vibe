//! Ignore-rule invalidation is scoped to visible directories and the whole batch.

use std::fs;
use std::sync::atomic::AtomicBool;

use notify::event::{DataChange, ModifyKind, RemoveKind};
use notify::EventKind;

use super::Workspace;

const MODIFIED: EventKind = EventKind::Modify(ModifyKind::Data(DataChange::Any));

#[test]
fn excluded_ignore_edits_and_deletions_do_not_walk_or_publish() {
    let mut ws = Workspace::new(&[
        (".ignore", "node_modules/\n"),
        ("node_modules/package/.gitignore", "old.log\n"),
        ("node_modules/package/.ignore", "old.log\n"),
        ("visible.txt", "v"),
    ]);
    ws.write("node_modules/package/.gitignore", "new.log\n");
    fs::remove_file(ws.directory.path().join("node_modules/package/.ignore")).unwrap();
    let stats = ws.apply(vec![
        ws.event(MODIFIED, "node_modules/package/.gitignore"),
        ws.event(
            EventKind::Remove(RemoveKind::File),
            "node_modules/package/.ignore",
        ),
    ]);
    assert_eq!(stats.walks, 0);
    assert!(!stats.rebuilt);
    assert_eq!(*ws.updates.borrow(), 0);
    ws.assert_matches_walk();
}

#[test]
fn rules_in_visible_directories_apply_even_if_the_rule_file_is_ignored() {
    let mut ws = Workspace::new(&[
        (".ignore", ".ignore\n"),
        ("visible/.ignore", ""),
        ("visible/hidden.tmp", "h"),
    ]);
    assert!(!ws.has("visible/.ignore"));
    ws.write("visible/.ignore", "*.tmp\n");
    let stats = ws.apply(vec![ws.event(MODIFIED, "visible/.ignore")]);
    assert_eq!(stats.walks, 1);
    assert!(stats.rebuilt);
    assert!(!ws.has("visible/hidden.tmp"));
    ws.assert_matches_walk();
}

#[test]
fn multiple_relevant_ignore_edits_rebuild_once() {
    let mut ws = Workspace::new(&[
        ("first/.ignore", ""),
        ("first/hidden.tmp", "h"),
        ("second/.ignore", ""),
        ("second/hidden.tmp", "h"),
    ]);
    ws.write("first/.ignore", "*.tmp\n");
    ws.write("second/.ignore", "*.tmp\n");
    let stats = ws.apply(vec![
        ws.event(MODIFIED, "first/.ignore"),
        ws.event(MODIFIED, "second/.ignore"),
    ]);
    assert_eq!(stats.walks, 1);
    assert!(stats.rebuilt);
    assert_eq!(*ws.updates.borrow(), 1);
    ws.assert_matches_walk();
}

#[test]
fn root_unignore_and_nested_rule_edit_use_the_latest_rules() {
    for root_first in [false, true] {
        let mut ws = Workspace::new(&[
            (".ignore", "excluded/\n"),
            ("excluded/.ignore", ""),
            ("excluded/hidden.tmp", "h"),
            ("excluded/visible.txt", "v"),
        ]);
        ws.write(".ignore", "");
        ws.write("excluded/.ignore", "*.tmp\n");
        let mut events = vec![
            ws.event(MODIFIED, "excluded/.ignore"),
            ws.event(MODIFIED, ".ignore"),
        ];
        if root_first {
            events.reverse();
        }
        let stats = ws.apply(events);
        assert_eq!(stats.walks, 1);
        assert!(ws.has("excluded/visible.txt"));
        assert!(!ws.has("excluded/hidden.tmp"));
        ws.assert_matches_walk();
    }
}

#[test]
fn deleting_a_relevant_ignore_file_restores_hidden_files() {
    let mut ws = Workspace::new(&[("sub/.ignore", "*.tmp\n"), ("sub/hidden.tmp", "h")]);
    fs::remove_file(ws.directory.path().join("sub/.ignore")).unwrap();
    let stats = ws.apply(vec![
        ws.event(EventKind::Remove(RemoveKind::File), "sub/.ignore")
    ]);
    assert_eq!(stats.walks, 1);
    assert!(ws.has("sub/hidden.tmp"));
    ws.assert_matches_walk();
}

#[test]
fn a_stopped_ignore_batch_never_publishes() {
    let mut ws = Workspace::new(&[(".ignore", ""), ("visible.txt", "v")]);
    ws.write(".ignore", "visible.txt\n");
    let stop = AtomicBool::new(true);
    let stats = ws.apply_stoppable(vec![ws.event(MODIFIED, ".ignore")], Some(&stop));
    assert_eq!(stats.walks, 0);
    assert!(ws.has("visible.txt"));
    assert_eq!(*ws.updates.borrow(), 0);
}

#[test]
fn a_root_rule_file_can_exclude_its_own_name_without_losing_invalidation() {
    let mut ws = Workspace::new(&[(".ignore", ".ignore\n"), ("visible.txt", "v")]);
    ws.write(".ignore", ".ignore\nvisible.txt\n");
    let stats = ws.apply(vec![ws.event(MODIFIED, ".ignore")]);
    assert_eq!(stats.walks, 1);
    assert!(!ws.has("visible.txt"));
    ws.assert_matches_walk();
}

#[test]
fn git_info_exclude_still_invalidates_a_filesystem_backed_index() {
    let mut ws = Workspace::new(&[(".git/info/exclude", ""), ("visible.txt", "v")]);
    ws.write(".git/info/exclude", "visible.txt\n");
    let stats = ws.apply(vec![ws.event(MODIFIED, ".git/info/exclude")]);
    assert_eq!(stats.walks, 1);
    assert!(stats.rebuilt);
    ws.assert_matches_walk();
}
