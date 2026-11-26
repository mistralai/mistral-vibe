//! Aggregated storage telemetry: buckets and counts, never a path, title or branch.

use serde_json::json;

use crate::app::App;
use crate::server::StorageUsage;
use crate::telemetry::{bmap, record};
use crate::utils::bytes::{bucket, count_bucket};

pub const PANEL_OPENED: &str = "vibe.storage_panel.opened";
pub const BULK_ARCHIVE_DONE: &str = "vibe.storage_bulk_archive.done";
pub const BULK_DELETE_DONE: &str = "vibe.storage_bulk_delete.done";
pub const LEFTOVER_CLEANUP_DONE: &str = "vibe.storage_leftover_cleanup.done";
pub const LOW_DISK_ALERT_UPDATED: &str = "vibe.storage_low_disk_alert.updated";
const SURFACE: &str = "cli";

pub fn opened(app: &App, usage: &StorageUsage) {
    let total = usage.snapshot.as_ref().map_or(0, |s| s.totals.total_bytes);
    let properties = json!({
        "total_bucket": bucket(total),
        "is_low": usage.disk.is_low,
        "surface": SURFACE,
    });
    record(app, PANEL_OPENED, bmap(properties));
}

pub struct BulkCounts {
    pub suggested: usize,
    pub selected: usize,
    pub archived: usize,
    /// Refused because the session works or another process holds it.
    pub refused: usize,
    pub estimated: u64,
    /// `None` when the user closed before the scan measured it.
    pub measured: Option<u64>,
    pub complete: bool,
}

pub fn bulk_archive(app: &App, counts: &BulkCounts) {
    let properties = json!({
        "status": if counts.archived > 0 { "success" } else { "error" },
        "suggested_bucket": count_bucket(counts.suggested),
        "selected_bucket": count_bucket(counts.selected),
        "archived_bucket": count_bucket(counts.archived),
        "refused_bucket": count_bucket(counts.refused),
        "estimated_freed_bucket": bucket(counts.estimated),
        "measured_freed_bucket": counts.measured.map(bucket),
        "is_complete": counts.complete,
        "surface": SURFACE,
    });
    record(app, BULK_ARCHIVE_DONE, bmap(properties));
}

pub struct BulkDeleteCounts {
    pub suggested: usize,
    pub selected: usize,
    pub deleted: usize,
    /// Refused because the session works or another process holds it.
    pub refused: usize,
    pub estimated: u64,
    /// What the server says the deletes freed.
    pub measured: u64,
}

pub fn bulk_delete(app: &App, counts: &BulkDeleteCounts) {
    let properties = json!({
        "status": if counts.deleted > 0 { "success" } else { "error" },
        "suggested_bucket": count_bucket(counts.suggested),
        "selected_bucket": count_bucket(counts.selected),
        "deleted_bucket": count_bucket(counts.deleted),
        "refused_bucket": count_bucket(counts.refused),
        "estimated_freed_bucket": bucket(counts.estimated),
        "measured_freed_bucket": bucket(counts.measured),
        "surface": SURFACE,
    });
    record(app, BULK_DELETE_DONE, bmap(properties));
}

pub struct CleanupCounts {
    /// Worktrees and folders the review offered.
    pub offered: usize,
    pub selected: usize,
    pub removed: usize,
    /// Kept by the server's re-check.
    pub kept: usize,
    pub estimated: u64,
}

pub fn leftover_cleanup(app: &App, counts: &CleanupCounts) {
    let properties = json!({
        "status": if counts.removed > 0 { "success" } else { "error" },
        "suggested_bucket": count_bucket(counts.offered),
        "selected_bucket": count_bucket(counts.selected),
        "removed_bucket": count_bucket(counts.removed),
        "refused_bucket": count_bucket(counts.kept),
        "estimated_freed_bucket": bucket(counts.estimated),
        "surface": SURFACE,
    });
    record(app, LEFTOVER_CLEANUP_DONE, bmap(properties));
}

/// `shown` when the notice mounts, `cleared` when a later startup finds the disk back above.
pub fn low_disk(app: &App, action: &str) {
    let properties = json!({"action": action, "surface": SURFACE});
    record(app, LOW_DISK_ALERT_UPDATED, bmap(properties));
}
