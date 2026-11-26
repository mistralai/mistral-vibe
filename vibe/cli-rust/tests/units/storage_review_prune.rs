//! `/storage` open reviews follow new numbers: ids a new snapshot no longer offers are dropped, and the kept rows read the same for deletes and cleanups.

use std::collections::HashSet;

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::server::StorageUsage;
use vibe_rs::storage_panel::outcome::kept_row;
use vibe_rs::storage_panel::{delete, leftovers, replace, Confirm, ReviewKind, View};

fn set(ids: &[&str]) -> HashSet<String> {
    ids.iter().map(|id| (*id).to_owned()).collect()
}

fn usage(delete_ids: &[&str], worktrees: &[&str], folders: &[&str]) -> StorageUsage {
    let sessions: Vec<_> = ["a", "b", "c"]
        .iter()
        .map(|id| json!({"sessionId": id, "archived": true, "deleteBytes": 10}))
        .collect();
    let rows: Vec<_> = worktrees
        .iter()
        .map(|id| json!({"id": id, "path": format!("/w/{id}"), "bytes": 5, "sessionIds": []}))
        .collect();
    let folders: Vec<_> = folders
        .iter()
        .map(|id| json!({"id": id, "path": format!("/w/{id}"), "bytes": 7}))
        .collect();
    serde_json::from_value(json!({"snapshot": {
        "sessions": sessions,
        "worktrees": rows,
        "deleteSuggestion": {"inactiveDays": 30, "sessionIds": delete_ids, "reclaimableBytes": 30},
        "leftoverCleanup": {"worktreeIds": worktrees, "unknownFolders": folders, "reclaimableBytes": 0}
    }}))
    .unwrap()
}

fn reviewing(kind: ReviewKind, usage: StorageUsage, order: &[&str]) -> App {
    let mut app = App::default();
    app.storage.open = true;
    app.storage.usage = usage;
    app.storage.view = View::Review;
    app.storage.review.kind = kind;
    app.storage.review.order = order.iter().map(|id| (*id).to_owned()).collect();
    app.storage.review.selected = set(order);
    app
}

#[test]
fn a_delete_review_drops_sessions_the_new_snapshot_no_longer_suggests() {
    let mut app = reviewing(
        ReviewKind::Delete,
        usage(&["a", "b", "c"], &[], &[]),
        &["a", "b", "c"],
    );
    replace(&mut app, usage(&["a", "c"], &[], &[]));
    assert_eq!(app.storage.review.order, ["a", "c"]);
    assert_eq!(app.storage.review.selected, set(&["a", "c"]));
    assert!(app.storage.view == View::Review);
    assert_eq!(delete::begin(&mut app, None), ["a", "c"]);
}

#[test]
fn a_cleanup_review_drops_items_already_gone_and_its_question_follows() {
    let before = usage(&[], &["w1", "w2"], &["f1"]);
    let mut app = reviewing(ReviewKind::Cleanup, before, &["w1", "w2", "f1"]);
    app.storage.confirm = Some(Confirm::Review {
        kind: ReviewKind::Cleanup,
        count: 3,
        frees: None,
    });
    replace(&mut app, usage(&[], &["w2"], &["f1"]));
    assert_eq!(app.storage.review.order, ["w2", "f1"]);
    let Some(Confirm::Review { count, .. }) = app.storage.confirm else {
        panic!("the question stays open");
    };
    assert_eq!(count, 2);
    let params = leftovers::begin(&mut app).unwrap();
    assert_eq!(
        (params.worktree_ids, params.folder_ids),
        (vec!["w2".to_owned()], vec!["f1".to_owned()])
    );
}

#[test]
fn a_review_left_empty_closes_and_never_sends_an_empty_request() {
    let mut app = reviewing(ReviewKind::Delete, usage(&["a"], &[], &[]), &["a"]);
    app.storage.confirm = Some(Confirm::Review {
        kind: ReviewKind::Delete,
        count: 1,
        frees: None,
    });
    replace(&mut app, usage(&[], &[], &[]));
    assert!(app.storage.view == View::Panel);
    assert!(app.storage.confirm.is_none());
    assert!(app.storage.review.order.is_empty());
    assert!(delete::begin(&mut app, None).is_empty());
    assert!(app.storage.busy.is_none());
}

#[test]
fn kept_rows_group_by_reason_for_sessions_and_items_alike() {
    let kept = [
        ("Santa".to_owned(), "in use"),
        ("Mirror".to_owned(), "already gone"),
        ("Flaky".to_owned(), "in use"),
    ];
    assert_eq!(
        kept_row(&kept, ("session", "sessions")).as_deref(),
        Some("Kept 3 sessions: in use (Santa, Flaky), already gone (Mirror).")
    );
    assert_eq!(kept_row(&[], ("item", "items")), None);
}
