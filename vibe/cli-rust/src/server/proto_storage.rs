//! Wire types for `storage/usage/*`, read leniently (ADR 0014).

use serde::{Deserialize, Serialize};

use crate::utils::json::{lenient, null_default};

/// `storage/usage/read` answer.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageUsage {
    #[serde(deserialize_with = "null_default")]
    pub status: String,
    pub snapshot: Option<StorageSnapshot>,
    #[serde(deserialize_with = "null_default")]
    pub disk: StorageDisk,
    pub stale: bool,
}

/// `storage/usage/updated` params: the read answer, its scan and whether every waited-for worktree went.
#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageUsageUpdated {
    #[serde(flatten)]
    pub usage: StorageUsage,
    /// The scan the update belongs to; absent on an older server.
    pub scan_id: Option<u64>,
    pub complete: bool,
}

impl Default for StorageUsageUpdated {
    fn default() -> Self {
        Self {
            usage: StorageUsage::default(),
            scan_id: None,
            complete: true,
        }
    }
}

impl StorageUsage {
    /// A scan is running: the numbers it will bring are not here yet.
    pub fn refreshing(&self) -> bool {
        self.status == "refreshing"
    }
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageDisk {
    pub total_bytes: u64,
    pub free_bytes: u64,
    pub is_low: bool,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageSnapshot {
    pub scan_started_at: u64,
    pub measured_at: u64,
    pub skipped_entry_count: u64,
    pub not_measured_count: u64,
    #[serde(deserialize_with = "null_default")]
    pub totals: StorageTotals,
    #[serde(deserialize_with = "lenient")]
    pub sessions: Vec<StorageSession>,
    #[serde(deserialize_with = "lenient")]
    pub worktrees: Vec<StorageWorktree>,
    #[serde(deserialize_with = "null_default")]
    pub archive_suggestion: StorageSuggestion,
    /// Archived sessions idle 30+ days; absent from an older server.
    #[serde(deserialize_with = "null_default")]
    pub delete_suggestion: StorageSuggestion,
    /// What `storage/leftovers/clean` would remove; absent from an older server.
    #[serde(deserialize_with = "null_default")]
    pub leftover_cleanup: StorageLeftoverCleanup,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageTotals {
    pub active_sessions_bytes: u64,
    pub archived_sessions_bytes: u64,
    pub leftover_worktrees_bytes: u64,
    pub other_vibe_data_bytes: u64,
    pub total_bytes: u64,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageSession {
    pub session_id: String,
    pub title: Option<String>,
    pub cwd: Option<String>,
    pub archived: bool,
    /// A process holds the session's lease.
    pub open: bool,
    pub last_activity_at: u64,
    pub history_bytes: u64,
    pub worktree_id: Option<String>,
    /// Every worktree the session claims, `worktree_id` first; empty from an older server.
    #[serde(deserialize_with = "lenient")]
    pub worktree_ids: Vec<String>,
    /// What deleting this session alone frees: its history and the worktrees only it claims; `None`
    /// when unknown (an older server or cache).
    pub delete_bytes: Option<u64>,
    /// A worktree `delete_bytes` counts is not measured yet.
    pub delete_bytes_at_least: bool,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageWorktree {
    pub id: String,
    pub path: String,
    pub branch: Option<String>,
    pub repo_name: Option<String>,
    /// `None`: not measured yet.
    pub bytes: Option<u64>,
    #[serde(deserialize_with = "lenient")]
    pub session_ids: Vec<String>,
    pub is_copy_on_write: bool,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageSuggestion {
    /// Idle this many days or more, as the server decides.
    pub inactive_days: u64,
    #[serde(deserialize_with = "lenient")]
    pub session_ids: Vec<String>,
    pub reclaimable_bytes: u64,
    /// A worktree it would free is not measured yet.
    pub reclaimable_at_least: bool,
}

/// Unclaimed managed worktrees (sized in `StorageSnapshot::worktrees`) and unknown folders of the worktree buckets.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageLeftoverCleanup {
    #[serde(deserialize_with = "lenient")]
    pub worktree_ids: Vec<String>,
    #[serde(deserialize_with = "lenient")]
    pub unknown_folders: Vec<StorageLeftoverFolder>,
    pub reclaimable_bytes: u64,
}

/// A folder in a worktree bucket with no claim and no `.git`: maybe the user's own.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageLeftoverFolder {
    pub id: String,
    pub path: String,
    pub bytes: u64,
}

/// `storage/leftovers/clean` params: the server checks each item again.
#[derive(Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StorageLeftoversCleanParams {
    pub worktree_ids: Vec<String>,
    pub folder_ids: Vec<String>,
}

/// `storage/leftovers/clean` answer: the ids removed, and the ones kept with their reason.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageLeftoversCleanResponse {
    #[serde(deserialize_with = "lenient")]
    pub removed: Vec<String>,
    #[serde(deserialize_with = "lenient")]
    pub kept: Vec<StorageLeftoverKept>,
}

/// `storage/sessions/delete` params: the server deletes each session, then the worktrees no session claims.
#[derive(Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StorageSessionsDeleteParams {
    pub session_ids: Vec<String>,
}

/// `storage/sessions/delete` answer: sessions and worktrees removed or kept, and the space freed.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageSessionsDeleteResponse {
    #[serde(deserialize_with = "lenient")]
    pub removed: Vec<String>,
    #[serde(deserialize_with = "lenient")]
    pub kept: Vec<StorageLeftoverKept>,
    #[serde(deserialize_with = "lenient")]
    pub removed_worktree_ids: Vec<String>,
    #[serde(deserialize_with = "lenient")]
    pub kept_worktrees: Vec<StorageLeftoverKept>,
    pub freed_bytes: u64,
    /// A removed worktree was not measured yet: `freed_bytes` is a floor.
    pub freed_bytes_at_least: bool,
}

/// A kept item and why; a session's `reason` is `in_use`, `not_found` or `error`, a folder's also
/// `claimed`, `kept_dirty`, `unsafe` or `too_large_to_check`.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageLeftoverKept {
    pub id: String,
    pub reason: String,
}

/// `storage/usage/refresh` params; absent fields keep the server defaults.
#[derive(Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StorageRefreshParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_age_ms: Option<u64>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub full: bool,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub wait_for_session_ids: Vec<String>,
}

/// `storage/usage/refresh` answer: the scan whose `storage/usage/updated` answers it.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StorageRefreshResponse {
    pub started: bool,
    pub scan_id: Option<u64>,
}

/// `session/archive` params: archive only an idle session.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionArchiveParams {
    pub session_id: String,
    pub archived: bool,
    pub require_idle: bool,
}
