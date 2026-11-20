//! `/storage` session delete through `storage/sessions/delete`: the estimate shown, the answer racing the server's refresh, and the wire shapes.

use std::collections::HashSet;

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::server::{
    method, StorageSessionsDeleteParams, StorageSessionsDeleteResponse, StorageSnapshot,
    StorageUsage,
};
use vibe_rs::storage_panel::delete::{begin, deleted, estimate, freed, row_estimate};
use vibe_rs::storage_panel::model::Estimate;
use vibe_rs::storage_panel::{actions, Action, Confirm, ReviewKind};

/// Three archived sessions suggested for delete; `a` and `b` share a worktree, `c` has its own.
fn usage(measured_at: u64) -> StorageUsage {
    serde_json::from_value(json!({
        "status": "up_to_date",
        "snapshot": {
            "measuredAt": measured_at,
            "sessions": [
                {"sessionId": "a", "title": "A", "archived": true, "historyBytes": 10, "worktreeId": "w-ab", "deleteBytes": 10},
                {"sessionId": "b", "title": "B", "archived": true, "historyBytes": 20, "worktreeId": "w-ab", "deleteBytes": 20},
                {"sessionId": "c", "title": "C", "archived": true, "historyBytes": 30, "worktreeId": "w-c", "deleteBytes": 530}
            ],
            "worktrees": [
                {"id": "w-ab", "path": "/w/ab", "bytes": 1_000, "sessionIds": ["a", "b"]},
                {"id": "w-c", "path": "/w/c", "bytes": 500, "sessionIds": ["c"]}
            ],
            "deleteSuggestion": {"sessionIds": ["a", "b", "c"], "reclaimableBytes": 1_560}
        }
    }))
    .unwrap()
}

fn set(ids: &[&str]) -> HashSet<String> {
    ids.iter().map(|id| (*id).to_owned()).collect()
}

fn snapshot() -> StorageSnapshot {
    usage(1_000).snapshot.unwrap()
}

#[test]
fn every_suggested_session_selected_shows_the_servers_total() {
    let mut snapshot = snapshot();
    let all = estimate(&snapshot, &set(&["a", "b", "c"]));
    assert_eq!(
        all,
        Some(Estimate {
            bytes: 1_560,
            at_least: false
        })
    );
    snapshot.delete_suggestion.reclaimable_at_least = true;
    let floor = estimate(&snapshot, &set(&["a", "b", "c"])).unwrap();
    assert!(floor.at_least);
}

#[test]
fn a_partial_selection_sums_the_rows_delete_bytes_as_a_floor() {
    let snapshot = snapshot();
    let pair = estimate(&snapshot, &set(&["a", "b"]));
    assert_eq!(
        pair,
        Some(Estimate {
            bytes: 30,
            at_least: true
        })
    );
    let one = estimate(&snapshot, &set(&["c"]));
    assert_eq!(
        one,
        Some(Estimate {
            bytes: 530,
            at_least: false
        })
    );
}

#[test]
fn unknown_or_unmeasured_delete_bytes_never_read_as_an_exact_size() {
    let mut snapshot = snapshot();
    snapshot.sessions[2].delete_bytes_at_least = true;
    let one = estimate(&snapshot, &set(&["c"])).unwrap();
    assert!(one.at_least, "an unmeasured worktree makes a floor");
    snapshot.sessions[0].delete_bytes = None;
    snapshot.sessions[1].delete_bytes = None;
    assert_eq!(estimate(&snapshot, &set(&["a", "b"])), None);
    let mixed = estimate(&snapshot, &set(&["a", "c"])).unwrap();
    assert_eq!((mixed.bytes, mixed.at_least), (530, true));
}

#[test]
fn a_row_delete_omits_the_size_when_unknown_and_says_at_least_when_unmeasured() {
    let row = |frees| Confirm::Row {
        action: Action::Delete,
        session_id: "a".into(),
        title: "A".into(),
        frees,
    };
    let mut session = snapshot().sessions[0].clone();
    session.delete_bytes = None;
    assert_eq!(
        actions::question(&row(row_estimate(&session))),
        "Delete \"A\"? The session history is deleted. Uncommitted work in its worktree is saved to a git ref."
    );
    session.delete_bytes = Some(2_000_000);
    session.delete_bytes_at_least = true;
    assert!(actions::question(&row(row_estimate(&session))).ends_with("Frees at least 2 MB."));
}

#[test]
fn only_y_confirms_what_removes_something_for_good() {
    let review = |kind| Confirm::Review {
        kind,
        count: 1,
        frees: None,
    };
    assert!(review(ReviewKind::Delete).destructive());
    assert!(review(ReviewKind::Cleanup).destructive());
    let row = |action| Confirm::Row {
        action,
        session_id: "a".into(),
        title: "A".into(),
        frees: None,
    };
    assert!(row(Action::Delete).destructive());
    assert!(!row(Action::Archive).destructive());
    assert!(!row(Action::Unarchive).destructive());
}

#[test]
fn the_delete_review_asks_first_with_the_estimate() {
    let confirm = Confirm::Review {
        kind: ReviewKind::Delete,
        count: 2,
        frees: estimate(&snapshot(), &set(&["a", "b"])),
    };
    assert_eq!(
        actions::question(&confirm),
        "Delete 2 archived sessions? Their history is deleted for good. Frees at least 30 B."
    );
    let cleanup = Confirm::Review {
        kind: ReviewKind::Cleanup,
        count: 1,
        frees: Some(Estimate {
            bytes: 2_100_000_000,
            at_least: false,
        }),
    };
    assert_eq!(
        actions::question(&cleanup),
        "Remove 1 item in worktrees for good? Frees about 2.1 GB."
    );
}

/// The `storage/usage/updated` of the scan the server starts after the delete: only `c` is left.
fn rescanned() -> serde_json::Value {
    json!({
        "status": "up_to_date",
        "complete": true,
        "snapshot": {
            "measuredAt": 2_000,
            "sessions": [{"sessionId": "c", "title": "C", "archived": true, "historyBytes": 30, "worktreeId": "w-c", "deleteBytes": 530}],
            "worktrees": [{"id": "w-c", "path": "/w/c", "bytes": 500, "sessionIds": ["c"]}],
            "deleteSuggestion": {"sessionIds": ["c"], "reclaimableBytes": 530}
        }
    })
}

/// What the panel shows once the delete answer and the refresh both arrived, in either order.
fn after_delete(update_first: bool) -> (Vec<String>, Vec<String>, u64, u64) {
    let mut app = App::default();
    app.storage.open = true;
    app.storage.usage = usage(1_000);
    app.storage.review.kind = ReviewKind::Delete;
    app.storage.review.order = vec!["a".into(), "b".into(), "c".into()];
    app.storage.review.selected = set(&["a", "b"]);
    assert_eq!(begin(&mut app, None), ["a", "b"]);
    let answer: StorageSessionsDeleteResponse = serde_json::from_value(json!({
        "removed": ["a", "b"],
        "kept": [],
        "removedWorktreeIds": ["w-ab"],
        "keptWorktrees": [],
        "freedBytes": 1_030
    }))
    .unwrap();
    if update_first {
        vibe_rs::storage_panel::on_updated(&mut app, &rescanned());
        deleted(&mut app, Ok(answer));
    } else {
        deleted(&mut app, Ok(answer));
        vibe_rs::storage_panel::on_updated(&mut app, &rescanned());
    }
    let snapshot = app.storage.usage.snapshot.as_ref().unwrap();
    let sessions = snapshot
        .sessions
        .iter()
        .map(|s| s.session_id.clone())
        .collect();
    let worktrees = snapshot.worktrees.iter().map(|w| w.id.clone()).collect();
    let suggested = snapshot.delete_suggestion.reclaimable_bytes;
    (sessions, worktrees, suggested, snapshot.measured_at)
}

#[test]
fn the_delete_answer_and_the_refresh_converge_in_either_order() {
    let answer_first = after_delete(false);
    assert_eq!(answer_first, after_delete(true));
    let expected = (vec!["c".to_owned()], vec!["w-c".to_owned()], 530, 2_000);
    assert_eq!(answer_first, expected);
}

#[test]
fn the_delete_request_and_answer_match_the_app_server_protocol() {
    assert_eq!(method::STORAGE_SESSIONS_DELETE, "storage/sessions/delete");
    let params = StorageSessionsDeleteParams {
        session_ids: vec!["s".into()],
    };
    assert_eq!(
        serde_json::to_value(params).unwrap(),
        json!({"sessionIds": ["s"]})
    );
    let answer: StorageSessionsDeleteResponse = vibe_rs::utils::json::read(&json!({
        "removed": ["s"],
        "kept": [{"id": "t", "reason": "in_use"}],
        "removedWorktreeIds": ["w"],
        "keptWorktrees": [{"id": "x", "reason": "kept_dirty"}],
        "freedBytes": 7
    }));
    assert_eq!(answer.removed, ["s"]);
    assert_eq!(answer.kept[0].reason, "in_use");
    assert_eq!(answer.removed_worktree_ids, ["w"]);
    assert_eq!(answer.kept_worktrees[0].id, "x");
    assert_eq!(answer.freed_bytes, 7);
    assert!(!freed(&answer).at_least);
    let older: StorageUsage =
        serde_json::from_value(json!({"snapshot": {"sessions": [{"sessionId": "s"}]}})).unwrap();
    let session = &older.snapshot.unwrap().sessions[0];
    assert_eq!(
        (session.delete_bytes, session.delete_bytes_at_least),
        (None, false)
    );
    let flags: StorageUsage = serde_json::from_value(json!({"snapshot": {
        "sessions": [{"sessionId": "s", "deleteBytes": null, "deleteBytesAtLeast": true}],
        "deleteSuggestion": {"inactiveDays": 30, "reclaimableAtLeast": true}
    }}))
    .unwrap();
    let snapshot = flags.snapshot.unwrap();
    assert!(snapshot.sessions[0].delete_bytes_at_least);
    assert!(snapshot.delete_suggestion.reclaimable_at_least);
    assert_eq!(snapshot.delete_suggestion.inactive_days, 30);
}

#[test]
fn an_unmeasured_removed_worktree_makes_the_freed_size_a_floor() {
    let answer: StorageSessionsDeleteResponse = vibe_rs::utils::json::read(&json!({
        "removed": ["s"],
        "removedWorktreeIds": ["w"],
        "freedBytes": 30,
        "freedBytesAtLeast": true
    }));
    let freed = freed(&answer);
    assert_eq!((freed.bytes, freed.at_least), (30, true));
    assert_eq!(freed.amount(), "at least 30 B");
}
