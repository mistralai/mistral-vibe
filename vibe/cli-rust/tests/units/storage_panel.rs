//! `/storage` pure logic: sizes, the archive estimate, rows, suggestions, the bulk scan match, the header and the wire shapes.

use std::collections::HashSet;

use serde_json::json;
use vibe_rs::server::{
    method, notification, SessionArchiveParams, StorageRefreshParams, StorageSnapshot,
    StorageUsage, StorageUsageUpdated,
};
use vibe_rs::storage_panel::bulk::{freed_estimate, matches, Match};
use vibe_rs::storage_panel::model::{age, estimate, measured_freed, rows, updated, Estimate};
use vibe_rs::storage_panel::review::review_order;
use vibe_rs::storage_panel::{still_running, ReviewKind};
use vibe_rs::ui::storage_rows;
use vibe_rs::utils::bytes::{bucket, count_bucket, decimal};

const GB: u64 = 1_000_000_000;
const MB: u64 = 1_000_000;
const DAY: u64 = 86_400_000;

#[test]
fn sizes_are_decimal_like_the_finder() {
    for (bytes, expected) in [
        (0, "0 B"),
        (999, "999 B"),
        (1_000, "1 KB"),
        (889 * MB, "889 MB"),
        (999_600_000, "1 GB"),
        (17_600 * MB, "17.6 GB"),
        (14 * GB, "14 GB"),
        (494 * GB, "494 GB"),
        (2_000 * GB, "2 TB"),
    ] {
        assert_eq!(decimal(bytes), expected, "{bytes}");
    }
}

#[test]
fn size_buckets_match_the_app_server() {
    assert_eq!(bucket(0), "<1 GB");
    assert_eq!(bucket(GB), "1–5 GB");
    assert_eq!(bucket(19 * GB), "5–20 GB");
    assert_eq!(bucket(49 * GB), "20–50 GB");
    assert_eq!(bucket(50 * GB), "50+ GB");
    for (count, expected) in [
        (0, "0"),
        (1, "1–10"),
        (10, "1–10"),
        (11, "11–50"),
        (50, "11–50"),
        (51, "51–100"),
        (100, "51–100"),
        (101, "100+"),
    ] {
        assert_eq!(count_bucket(count), expected, "{count}");
    }
}

fn snapshot() -> StorageSnapshot {
    let usage: StorageUsage = serde_json::from_value(json!({
        "snapshot": {
            "measuredAt": 0,
            "sessions": [
                {"sessionId": "a", "title": "A", "historyBytes": 10, "worktreeId": "w-shared", "lastActivityAt": 3 * DAY},
                {"sessionId": "b", "title": "", "historyBytes": 20, "worktreeId": "w-shared", "open": true, "lastActivityAt": DAY},
                {"sessionId": "c", "title": "C", "historyBytes": 30, "worktreeId": "w-own", "lastActivityAt": 2 * DAY},
                {"sessionId": "d", "title": "D", "historyBytes": 5, "worktreeId": "w-new", "archived": true},
                {"sessionId": "e", "title": "E", "historyBytes": 1, "worktreeId": "w-new", "lastActivityAt": 0}
            ],
            "worktrees": [
                {"id": "w-shared", "path": "/p/shared", "bytes": 1_000, "sessionIds": ["a", "b"]},
                {"id": "w-own", "path": "/p/own", "bytes": 500, "sessionIds": ["c"], "repoName": "repo"},
                {"id": "w-new", "path": "/p/new", "bytes": null, "sessionIds": ["d", "e"]}
            ],
            "archiveSuggestion": {"sessionIds": ["a", "c", "e"], "reclaimableBytes": 500}
        }
    }))
    .unwrap();
    usage.snapshot.unwrap()
}

fn set(ids: &[&str]) -> HashSet<String> {
    ids.iter().map(|id| (*id).to_owned()).collect()
}

#[test]
fn a_shared_worktree_counts_only_when_every_session_goes() {
    let snapshot = snapshot();
    assert_eq!(estimate(&snapshot, &set(&["a"])), Estimate::default());
    let both = estimate(&snapshot, &set(&["a", "b"]));
    assert_eq!(
        both,
        Estimate {
            bytes: 1_000,
            at_least: false
        }
    );
}

#[test]
fn the_measured_space_is_the_archived_worktrees_the_scan_no_longer_finds() {
    let before = snapshot();
    // The scan after archiving c: its worktree is gone, and an unrelated
    // session's history grew by much more meanwhile.
    let mut after = snapshot();
    after.worktrees.retain(|w| w.id != "w-own");
    after.totals.total_bytes = before.totals.total_bytes + 10_000;
    assert_eq!(measured_freed(&before, &after, &set(&["c"])), 500);
    // A worktree the scan still finds frees nothing yet.
    assert_eq!(measured_freed(&before, &snapshot(), &set(&["c"])), 0);
    // A worktree another selected-but-unarchived session still uses is not freed.
    let mut shared_gone = snapshot();
    shared_gone.worktrees.retain(|w| w.id != "w-shared");
    assert_eq!(measured_freed(&before, &shared_gone, &set(&["a"])), 0);
}

#[test]
fn an_archived_peer_frees_the_worktree_and_an_unmeasured_one_reads_at_least() {
    let snapshot = snapshot();
    let picked = estimate(&snapshot, &set(&["c", "e"]));
    assert_eq!(
        picked,
        Estimate {
            bytes: 500,
            at_least: true
        }
    );
}

#[test]
fn an_unmeasured_worktree_outside_the_selection_never_reads_at_least() {
    let snapshot = snapshot();
    assert!(!estimate(&snapshot, &set(&["c"])).at_least);
}

#[test]
fn rows_sort_by_size_with_badges() {
    let snapshot = snapshot();
    let rows = rows(&snapshot, Some("c"));
    let order: Vec<&str> = rows.iter().map(|r| r.session.session_id.as_str()).collect();
    assert_eq!(order, ["b", "a", "c", "d", "e"]);
    assert_eq!(rows[0].title(), "Untitled session");
    assert_eq!(rows[0].badges(), "open · shared by 2");
    assert!(rows[0].locked());
    assert_eq!(rows[2].badges(), "this session");
    assert_eq!(rows[2].project(), "repo");
    assert_eq!(
        rows[3].badges(),
        "archived · shared by 2 · not measured yet"
    );
}

#[test]
fn the_review_sorts_by_size_or_oldest_first() {
    let snapshot = snapshot();
    let order = |by_date| review_order(&snapshot, ReviewKind::Archive, None, by_date);
    assert_eq!(order(false), ["a", "c", "e"]);
    assert_eq!(order(true), ["e", "c", "a"]);
}

#[test]
fn ages_and_update_times_read_as_words() {
    assert_eq!(age(10 * DAY, 10 * DAY + 1), "today");
    assert_eq!(age(0, 42 * DAY + 5), "42d ago");
    assert_eq!(updated(0, 30_000), "updated just now");
    assert_eq!(updated(0, 120_000), "updated 2 minutes ago");
    assert_eq!(updated(0, 3_600_000), "updated 1 hour ago");
}

#[test]
fn wire_shapes_match_the_app_server_protocol() {
    assert_eq!(method::SESSION_ARCHIVE, "session/archive");
    assert_eq!(method::STORAGE_USAGE_READ, "storage/usage/read");
    assert_eq!(method::STORAGE_USAGE_REFRESH, "storage/usage/refresh");
    assert_eq!(notification::STORAGE_USAGE_UPDATED, "storage/usage/updated");
    let archive = SessionArchiveParams {
        session_id: "s".into(),
        archived: true,
        require_idle: true,
    };
    let archive = serde_json::to_value(archive).unwrap();
    assert_eq!(
        archive,
        json!({"sessionId": "s", "archived": true, "requireIdle": true})
    );
    let plain = serde_json::to_value(StorageRefreshParams::default()).unwrap();
    assert_eq!(plain, json!({}));
    let waiting = StorageRefreshParams {
        wait_for_session_ids: vec!["s".into()],
        ..Default::default()
    };
    let waiting = serde_json::to_value(waiting).unwrap();
    assert_eq!(waiting, json!({"waitForSessionIds": ["s"]}));
    let updated: StorageUsageUpdated = serde_json::from_value(json!({
        "status": "up_to_date",
        "snapshot": {"measuredAt": 5},
        "disk": {"totalBytes": 10},
        "stale": true,
        "eventId": 3,
        "complete": false,
        "scanId": 7,
    }))
    .unwrap();
    assert_eq!(updated.usage.status, "up_to_date");
    assert_eq!(updated.usage.snapshot.unwrap().measured_at, 5);
    assert_eq!(updated.usage.disk.total_bytes, 10);
    assert!(updated.usage.stale);
    assert_eq!(updated.scan_id, Some(7));
    assert!(!updated.complete);
    let older: StorageUsageUpdated = vibe_rs::utils::json::read(&json!({"status": "failed"}));
    assert_eq!(older.scan_id, None);
    assert!(older.complete);
}

#[test]
fn a_refresh_answer_naming_a_settled_scan_is_not_running() {
    assert!(!still_running(Some(4), Some(4)));
    assert!(!still_running(Some(3), Some(4)));
    assert!(still_running(Some(5), Some(4)));
    assert!(still_running(Some(5), None));
    assert!(still_running(None, Some(4)));
}

#[test]
fn a_read_tolerates_missing_and_unknown_fields() {
    let usage: StorageUsage =
        serde_json::from_value(json!({"status": "refreshing", "snapshot": null, "future": 1}))
            .unwrap();
    assert!(usage.refreshing());
    assert!(usage.snapshot.is_none());
    assert_eq!(usage.disk.total_bytes, 0);
}

#[test]
fn the_startup_notice_points_to_the_command() {
    assert_eq!(
        vibe_rs::storage_panel::startup::notice(14 * GB, 494 * GB),
        "Low disk space: 14 GB free of 494 GB. Run /storage to see what Vibe uses."
    );
}

fn update(status: &str, scan_id: Option<u64>, scan_started_at: u64) -> StorageUsageUpdated {
    serde_json::from_value(json!({
        "status": status,
        "scanId": scan_id,
        "snapshot": {"scanStartedAt": scan_started_at, "measuredAt": scan_started_at},
    }))
    .unwrap()
}

#[test]
fn only_the_named_scan_ends_a_bulk_wait() {
    let named = Some(Some(7));
    assert_eq!(
        matches(named, 100, &update("up_to_date", Some(7), 50)),
        Match::Ours
    );
    assert_eq!(
        matches(named, 100, &update("failed", Some(7), 50)),
        Match::Ours
    );
    assert_eq!(
        matches(named, 100, &update("up_to_date", Some(6), 200)),
        Match::Other
    );
    assert_eq!(
        matches(named, 100, &update("failed", Some(8), 200)),
        Match::Other
    );
}

#[test]
fn a_scan_start_never_ends_a_bulk_wait() {
    // The named scan's `refreshing` start, before and after the refresh answers.
    assert_eq!(
        matches(Some(Some(7)), 100, &update("refreshing", Some(7), 50)),
        Match::Other
    );
    assert_eq!(
        matches(None, 100, &update("refreshing", Some(7), 50)),
        Match::Other
    );
    // A server without scan ids: a start whose cached snapshot looks recent.
    assert_eq!(
        matches(Some(None), 100, &update("refreshing", None, 200)),
        Match::Other
    );
}

#[test]
fn the_result_row_says_the_measured_drop_once_the_scan_saw_it() {
    let estimated = Estimate {
        bytes: 1_800_000_000,
        at_least: true,
    };
    // Measured: the drop, exact, even though the estimate read "at least".
    assert_eq!(
        freed_estimate(Some(1_761_000_000), estimated),
        Estimate {
            bytes: 1_761_000_000,
            at_least: false
        }
    );
    // Not measured (Esc, a failed or incomplete scan) or no drop yet: the estimate.
    assert_eq!(freed_estimate(None, estimated), estimated);
    assert_eq!(freed_estimate(Some(0), estimated), estimated);
}

#[test]
fn a_tagged_result_before_the_answer_waits_for_it() {
    assert_eq!(
        matches(None, 100, &update("up_to_date", Some(6), 200)),
        Match::Early(6)
    );
    assert_eq!(
        matches(None, 100, &update("failed", Some(7), 50)),
        Match::Early(7)
    );
}

#[test]
fn a_server_without_scan_ids_takes_a_success_after_the_archives_or_any_failure() {
    let untagged = Some(None);
    assert_eq!(
        matches(untagged, 100, &update("up_to_date", None, 100)),
        Match::Ours
    );
    assert_eq!(
        matches(untagged, 100, &update("up_to_date", None, 99)),
        Match::Other
    );
    assert_eq!(
        matches(untagged, 100, &update("failed", None, 50)),
        Match::Ours
    );
    assert_eq!(
        matches(Some(Some(7)), 100, &update("failed", None, 50)),
        Match::Other
    );
    assert_eq!(
        matches(None, 100, &update("up_to_date", None, 200)),
        Match::Ours
    );
}

#[test]
fn the_header_says_refreshing_beside_the_update_time() {
    let usage: StorageUsage = serde_json::from_value(json!({
        "status": "up_to_date",
        "snapshot": {"measuredAt": 0},
    }))
    .unwrap();
    let text = |refreshing| -> String {
        vibe_rs::ui::storage_rows::header(&usage, refreshing, 3 * 3_600_000)
            .into_iter()
            .map(|(text, _)| text)
            .collect()
    };
    assert!(text(true).ends_with("updated 3 hours ago · refreshing…"));
    assert!(text(false).ends_with("updated 3 hours ago"));
}

fn text(runs: Vec<(String, ratatui::style::Style)>) -> String {
    runs.into_iter().map(|(text, _)| text).collect()
}

/// `a` claims two worktrees, one shared with `b`; `old` and `older` are archived and idle 30+ days.
fn claims() -> StorageSnapshot {
    let usage: StorageUsage = serde_json::from_value(json!({
        "snapshot": {
            "sessions": [
                {"sessionId": "a", "historyBytes": 10, "worktreeId": "w1", "worktreeIds": ["w1", "w2"]},
                {"sessionId": "b", "historyBytes": 20, "worktreeId": "w2"},
                {"sessionId": "old", "archived": true, "historyBytes": 100, "worktreeId": "w3", "lastActivityAt": DAY},
                {"sessionId": "older", "archived": true, "historyBytes": 200, "worktreeId": "w3", "lastActivityAt": DAY},
                {"sessionId": "busy", "archived": true, "open": true, "historyBytes": 400, "lastActivityAt": DAY}
            ],
            "worktrees": [
                {"id": "w1", "path": "/p/1", "bytes": 1_000, "sessionIds": ["a"], "branch": "one"},
                {"id": "w2", "path": "/p/2", "bytes": 500, "sessionIds": ["a", "b"], "branch": "two"},
                {"id": "w3", "path": "/p/3", "bytes": 3_000, "sessionIds": ["old", "older"]}
            ],
            "archiveSuggestion": {"inactiveDays": 30, "sessionIds": ["a"], "reclaimableBytes": 1_000},
            "deleteSuggestion": {"inactiveDays": 30, "sessionIds": ["old", "older", "busy"], "reclaimableBytes": 37_000}
        }
    }))
    .unwrap();
    usage.snapshot.unwrap()
}

#[test]
fn a_row_counts_every_worktree_its_session_claims() {
    let snapshot = claims();
    let rows = rows(&snapshot, None);
    let a = rows.iter().find(|r| r.session.session_id == "a").unwrap();
    assert_eq!(a.bytes(), 1_510);
    assert_eq!(a.badges(), "2 worktrees · shared by 2");
    let details: Vec<String> = storage_rows::details(a).into_iter().map(text).collect();
    assert_eq!(
        details,
        [
            "history 10 B  ·  worktree 1 KB on one",
            "worktree 500 B on two  ·  shared by 2"
        ]
    );
}

#[test]
fn an_older_server_without_worktree_ids_falls_back_to_the_worktree_id() {
    let snapshot = claims();
    let rows = rows(&snapshot, None);
    let b = rows.iter().find(|r| r.session.session_id == "b").unwrap();
    assert_eq!(b.bytes(), 520);
    assert_eq!(b.worktrees.len(), 1);
}

#[test]
fn the_delete_review_leaves_out_open_sessions() {
    let snapshot = claims();
    let order = review_order(&snapshot, ReviewKind::Delete, None, false);
    assert_eq!(order, ["older", "old"]);
}

#[test]
fn the_suggestions_say_active_and_archived_sessions() {
    let snapshot = claims();
    let archive = text(storage_rows::suggestion(
        &snapshot,
        ReviewKind::Archive,
        false,
    ));
    assert_eq!(
        archive,
        "Archive 1 active session inactive for 30+ days  ·  frees about 1 KB →"
    );
    let delete = text(storage_rows::suggestion(
        &snapshot,
        ReviewKind::Delete,
        false,
    ));
    assert_eq!(
        delete,
        "Delete 3 archived sessions inactive for 30+ days  ·  frees about 37 KB →"
    );
}

#[test]
fn the_old_archived_hint_shows_only_when_no_suggested_session_can_be_deleted_here() {
    let mut snapshot = claims();
    assert!(storage_rows::old_archived(&snapshot, None).is_none());
    snapshot.delete_suggestion.session_ids = vec!["busy".into(), "older".into()];
    snapshot.delete_suggestion.inactive_days = 45;
    assert!(storage_rows::old_archived(&snapshot, None).is_none());
    let hint = storage_rows::old_archived(&snapshot, Some("older")).map(text);
    assert!(!vibe_rs::storage_panel::review::offered(
        &snapshot,
        ReviewKind::Delete,
        Some("older")
    ));
    assert_eq!(
        hint.as_deref(),
        Some("2 archived sessions inactive for 45+ days are open, so they can't be deleted now.")
    );
    snapshot.delete_suggestion.session_ids = vec!["busy".into()];
    assert_eq!(
        storage_rows::old_archived(&snapshot, None)
            .map(text)
            .as_deref(),
        Some("1 archived session inactive for 45+ days is open, so it can't be deleted now.")
    );
    snapshot.delete_suggestion.session_ids.clear();
    assert!(storage_rows::old_archived(&snapshot, Some("older")).is_none());
}

#[test]
fn the_suggestion_rows_honor_the_servers_lower_bound_and_days() {
    let mut snapshot = claims();
    snapshot.delete_suggestion.reclaimable_at_least = true;
    snapshot.archive_suggestion.inactive_days = 14;
    let delete = text(storage_rows::suggestion(
        &snapshot,
        ReviewKind::Delete,
        false,
    ));
    assert!(delete.ends_with("frees at least 37 KB →"), "{delete}");
    let archive = text(storage_rows::suggestion(
        &snapshot,
        ReviewKind::Archive,
        false,
    ));
    assert!(
        archive.starts_with("Archive 1 active session inactive for 14+ days"),
        "{archive}"
    );
}
