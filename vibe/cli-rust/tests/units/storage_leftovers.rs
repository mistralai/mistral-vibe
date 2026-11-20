//! `/storage` leftover cleanup: what is offered, the estimate, the rows, the answer racing the server's refresh, and the wire shapes.

use std::collections::HashSet;

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::server::{
    method, StorageLeftoversCleanParams, StorageLeftoversCleanResponse, StorageSnapshot,
    StorageUsage,
};
use vibe_rs::storage_panel::leftovers::{begin, cleaned, items, kept_text, suggestion_text};
use vibe_rs::storage_panel::model::Estimate;
use vibe_rs::storage_panel::review::{offered, review_estimate, review_order};
use vibe_rs::storage_panel::ReviewKind;
use vibe_rs::ui::storage_rows;

/// Two unclaimed worktrees (one not measured yet), one claimed, and two unknown folders.
fn snapshot() -> StorageSnapshot {
    let usage: StorageUsage = serde_json::from_value(json!({
        "snapshot": {
            "worktrees": [
                {"id": "repo-aaaa/old", "path": "/w/repo-aaaa/old", "bytes": 1_000, "sessionIds": [], "repoName": "repo", "branch": "old"},
                {"id": "repo-aaaa/new", "path": "/w/repo-aaaa/new", "bytes": null, "sessionIds": []},
                {"id": "repo-aaaa/used", "path": "/w/repo-aaaa/used", "bytes": 9_000, "sessionIds": ["s"]}
            ],
            "leftoverCleanup": {
                "worktreeIds": ["repo-aaaa/old", "repo-aaaa/new"],
                "unknownFolders": [
                    {"id": "repo-aaaa/target", "path": "/w/repo-aaaa/target", "bytes": 300},
                    {"id": "repo-aaaa/tmp", "path": "/w/repo-aaaa/tmp", "bytes": 20}
                ],
                "reclaimableBytes": 13_200
            }
        }
    }))
    .unwrap();
    usage.snapshot.unwrap()
}

fn set(ids: &[&str]) -> HashSet<String> {
    ids.iter().map(|id| (*id).to_owned()).collect()
}

fn text(runs: Vec<(String, ratatui::style::Style)>) -> String {
    runs.into_iter().map(|(text, _)| text).collect()
}

#[test]
fn every_leftover_is_offered_worktrees_first_sized_from_the_snapshot() {
    let snapshot = snapshot();
    let order = review_order(&snapshot, ReviewKind::Cleanup, None, false);
    assert_eq!(
        order,
        [
            "repo-aaaa/old",
            "repo-aaaa/new",
            "repo-aaaa/target",
            "repo-aaaa/tmp"
        ]
    );
    let items = items(&snapshot);
    assert_eq!(items[0].path, "/w/repo-aaaa/old");
    assert_eq!(items[0].bytes, Some(1_000));
    assert!(items[0].worktree && !items[2].worktree);
    assert!(offered(&snapshot, ReviewKind::Cleanup, None));
    assert!(!offered(
        &StorageSnapshot::default(),
        ReviewKind::Cleanup,
        None
    ));
}

#[test]
fn the_estimate_sums_the_selected_items_and_reads_at_least_for_an_unmeasured_worktree() {
    let snapshot = snapshot();
    let estimate = |ids: &[&str]| review_estimate(&snapshot, ReviewKind::Cleanup, &set(ids));
    let picked = estimate(&["repo-aaaa/old", "repo-aaaa/target"]);
    assert_eq!(
        picked,
        Some(Estimate {
            bytes: 1_300,
            at_least: false
        })
    );
    let unmeasured = estimate(&["repo-aaaa/new", "repo-aaaa/tmp"]);
    assert_eq!(
        unmeasured,
        Some(Estimate {
            bytes: 20,
            at_least: true
        })
    );
    assert_eq!(estimate(&["repo-aaaa/used"]), Some(Estimate::default()));
}

#[test]
fn the_suggestion_names_worktrees_and_folders() {
    let mut snapshot = snapshot();
    let row = text(storage_rows::suggestion(
        &snapshot,
        ReviewKind::Cleanup,
        false,
    ));
    assert_eq!(
        row,
        "Clean up 2 leftover worktrees and 2 folders  ·  frees at least 13 KB →"
    );
    snapshot.leftover_cleanup.worktree_ids.clear();
    assert_eq!(suggestion_text(&snapshot), "Clean up 2 leftover folders");
    snapshot.leftover_cleanup.worktree_ids = vec!["repo-aaaa/old".into()];
    snapshot.leftover_cleanup.unknown_folders.clear();
    assert_eq!(suggestion_text(&snapshot), "Clean up 1 leftover worktree");
}

#[test]
fn kept_items_are_grouped_by_reason_in_words() {
    let snapshot = snapshot();
    let answer: StorageLeftoversCleanResponse = serde_json::from_value(json!({
        "removed": ["repo-aaaa/old"],
        "kept": [
            {"id": "repo-aaaa/target", "reason": "in_use"},
            {"id": "repo-aaaa/tmp", "reason": "in_use"},
            {"id": "repo-aaaa/new", "reason": "kept_dirty"},
            {"id": "repo-aaaa/old", "reason": "too_large_to_check"}
        ]
    }))
    .unwrap();
    assert_eq!(
        kept_text(&answer.kept, &items(&snapshot), ("item", "items")).as_deref(),
        Some("Kept 4 items: in use (target, tmp), its uncommitted work could not be saved (new), too large to check that nothing uses it (old).")
    );
    assert_eq!(kept_text(&[], &items(&snapshot), ("item", "items")), None);
}

#[test]
fn the_clean_request_and_answer_match_the_app_server_protocol() {
    assert_eq!(method::STORAGE_LEFTOVERS_CLEAN, "storage/leftovers/clean");
    let params = StorageLeftoversCleanParams {
        worktree_ids: vec!["w".into()],
        folder_ids: vec![],
    };
    assert_eq!(
        serde_json::to_value(params).unwrap(),
        json!({"worktreeIds": ["w"], "folderIds": []})
    );
    let older: StorageLeftoversCleanResponse = vibe_rs::utils::json::read(&json!({}));
    assert!(older.removed.is_empty() && older.kept.is_empty());
    let usage: StorageUsage =
        serde_json::from_value(json!({"snapshot": {"leftoverCleanup": null}})).unwrap();
    let cleanup = usage.snapshot.unwrap().leftover_cleanup;
    assert!(cleanup.worktree_ids.is_empty() && cleanup.unknown_folders.is_empty());
}

/// Two leftover worktrees and a folder, measured at `measured_at`.
fn usage(measured_at: u64) -> StorageUsage {
    serde_json::from_value(json!({
        "status": "up_to_date",
        "snapshot": {
            "measuredAt": measured_at,
            "worktrees": [
                {"id": "repo-aaaa/old", "path": "/w/repo-aaaa/old", "bytes": 1_000, "sessionIds": []},
                {"id": "repo-aaaa/kept", "path": "/w/repo-aaaa/kept", "bytes": 50, "sessionIds": []}
            ],
            "leftoverCleanup": {
                "worktreeIds": ["repo-aaaa/old", "repo-aaaa/kept"],
                "unknownFolders": [{"id": "repo-aaaa/target", "path": "/w/repo-aaaa/target", "bytes": 300}],
                "reclaimableBytes": 1_350
            }
        }
    }))
    .unwrap()
}

/// The `storage/usage/updated` of the scan the server starts after the clean: only `kept` is left.
fn rescanned() -> serde_json::Value {
    json!({
        "status": "up_to_date",
        "complete": true,
        "snapshot": {
            "measuredAt": 2_000,
            "worktrees": [{"id": "repo-aaaa/kept", "path": "/w/repo-aaaa/kept", "bytes": 50, "sessionIds": []}],
            "leftoverCleanup": {"worktreeIds": ["repo-aaaa/kept"], "unknownFolders": [], "reclaimableBytes": 50}
        }
    })
}

/// A panel in the cleanup review with `selected` checked, its clean request sent.
fn cleaning(selected: &[&str]) -> App {
    let mut app = App::default();
    app.storage.open = true;
    app.storage.usage = usage(1_000);
    app.storage.review.kind = ReviewKind::Cleanup;
    app.storage.review.order = review_order(
        &usage(1_000).snapshot.unwrap(),
        ReviewKind::Cleanup,
        None,
        false,
    );
    app.storage.review.selected = set(selected);
    begin(&mut app).unwrap();
    app
}

fn removed(ids: &[&str]) -> Result<StorageLeftoversCleanResponse, String> {
    Ok(serde_json::from_value(json!({"removed": ids, "kept": []})).unwrap())
}

/// What the panel shows once the clean answer and the refresh both arrived, in either order.
fn after_cleanup(update_first: bool) -> (Vec<String>, u64, u64, String) {
    let mut app = cleaning(&["repo-aaaa/old", "repo-aaaa/target"]);
    let answer = removed(&["repo-aaaa/old", "repo-aaaa/target"]);
    if update_first {
        vibe_rs::storage_panel::on_updated(&mut app, &rescanned());
        cleaned(&mut app, answer);
    } else {
        cleaned(&mut app, answer);
        vibe_rs::storage_panel::on_updated(&mut app, &rescanned());
    }
    let snapshot = app.storage.usage.snapshot.as_ref().unwrap();
    let row = text(storage_rows::suggestion(
        snapshot,
        ReviewKind::Cleanup,
        false,
    ));
    let ids = items(snapshot).into_iter().map(|item| item.id).collect();
    (
        ids,
        snapshot.leftover_cleanup.reclaimable_bytes,
        snapshot.measured_at,
        row,
    )
}

#[test]
fn the_clean_answer_and_the_refresh_converge_in_either_order() {
    let answer_first = after_cleanup(false);
    assert_eq!(answer_first, after_cleanup(true));
    let expected = (
        vec!["repo-aaaa/kept".to_owned()],
        50,
        2_000,
        "Clean up 1 leftover worktree  ·  frees about 50 B →".to_owned(),
    );
    assert_eq!(answer_first, expected);
}

#[test]
fn the_clean_answer_alone_recomputes_what_is_left_from_the_remaining_items() {
    let mut app = cleaning(&["repo-aaaa/target"]);
    cleaned(&mut app, removed(&["repo-aaaa/target"]));
    let snapshot = app.storage.usage.snapshot.as_ref().unwrap();
    assert!(snapshot.leftover_cleanup.unknown_folders.is_empty());
    assert_eq!(snapshot.leftover_cleanup.reclaimable_bytes, 1_050);
    assert_eq!(snapshot.measured_at, 1_000);
}
