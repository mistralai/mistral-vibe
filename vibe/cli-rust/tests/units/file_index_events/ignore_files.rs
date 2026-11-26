//! Ignore-rule invalidation is scoped to visible directories and the whole batch.

use std::fs;
use std::path::Path;
use std::process::Command;
use std::sync::atomic::AtomicBool;

use notify::event::{CreateKind, DataChange, ModifyKind, RemoveKind, RenameMode};
use notify::EventKind;
use vibe_rs::utils::file_index::rebuild_index;

use super::Workspace;

const MODIFIED: EventKind = EventKind::Modify(ModifyKind::Data(DataChange::Any));
const CREATED: EventKind = EventKind::Create(CreateKind::File);
const RENAMED_FROM: EventKind = EventKind::Modify(ModifyKind::Name(RenameMode::From));
const RENAMED_TO: EventKind = EventKind::Modify(ModifyKind::Name(RenameMode::To));

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

#[test]
fn removing_excluded_rules_with_their_directory_keeps_later_rules_fresh() {
    let mut ws = Workspace::new(&[
        (".ignore", "node_modules/\n"),
        ("app/node_modules/pkg/.gitignore", "x\n"),
    ]);
    fs::remove_dir_all(ws.directory.path().join("app")).unwrap();
    ws.apply(vec![
        ws.event(
            EventKind::Remove(RemoveKind::File),
            "app/node_modules/pkg/.gitignore",
        ),
        ws.event(EventKind::Remove(RemoveKind::Folder), "app"),
    ]);
    ws.write("app/.ignore", "secret*\n");
    ws.apply(vec![ws.event(EventKind::Create(CreateKind::Folder), "app")]);
    ws.write("app/secret.txt", "s");
    ws.apply(vec![ws.event(CREATED, "app/secret.txt")]);
    assert!(!ws.has("app/secret.txt"));
    ws.assert_matches_walk();
}

#[test]
fn a_directory_moved_over_a_cached_one_uses_its_own_rules() {
    let mut ws = Workspace::new(&[
        ("pkg/.ignore", "out/\n"),
        ("pkg/out/gen.txt", "g"),
        ("other/out/gen.txt", "g"),
    ]);
    for (from, to) in [("pkg", "old"), ("other", "pkg")] {
        let root = ws.directory.path();
        fs::rename(root.join(from), root.join(to)).unwrap();
        ws.apply(vec![ws.event(RENAMED_FROM, from), ws.event(RENAMED_TO, to)]);
    }
    ws.write("pkg/out/.ignore", "*.txt\n");
    ws.apply(vec![ws.event(CREATED, "pkg/out/.ignore")]);
    assert!(!ws.has("pkg/out/gen.txt"));
    ws.assert_matches_walk();
}

#[test]
fn exclude_edits_reach_worktrees_of_an_ignored_repository() {
    let mut ws = Workspace::new(&[(".ignore", "repo/\n"), ("repo/README", "r")]);
    let repo = ws.directory.path().join("repo");
    git(&repo, &["init", "-q"]);
    git(&repo, &["add", "."]);
    git(&repo, &["commit", "-qm", "init"]);
    git(&repo, &["worktree", "add", "-q", "../wt"]);
    ws.write("wt/a.log", "l");
    *ws.paths.write().unwrap() = rebuild_index(ws.directory.path(), None).0;
    assert!(ws.has("wt/a.log"));
    ws.write("repo/.git/info/exclude", "*.log\n");
    ws.apply(vec![ws.event(MODIFIED, "repo/.git/info/exclude")]);
    assert!(!ws.has("wt/a.log"));
    ws.assert_matches_walk();
}

fn git(directory: &Path, args: &[&str]) {
    let output = Command::new("git")
        .arg("-C")
        .arg(directory)
        .args(["-c", "user.name=vibe", "-c", "user.email=vibe@example.com"])
        .args(["-c", "commit.gpgsign=false"])
        .args(args)
        .output()
        .expect("git binary available");
    assert!(output.status.success(), "git {args:?} failed: {output:?}");
}
