//! The what's-new seen-version gate over the shared `[update_cache]` section
//! of `cache.toml` (Python `update_notifier/whats_new.py`); the update checks
//! that seed the cache live in `update_notifier`.

use super::now_unix;
use crate::update_notifier::cache::{
    FileSystemUpdateCacheRepository, UpdateCache, UpdateCacheRepository,
};

/// Python `should_show_whats_new`: show when a stored cache exists and its seen
/// version is absent or differs from the current one. No cache at all (or a
/// section missing the required fields) shows nothing and stamps nothing.
pub fn should_show(current_version: &str) -> bool {
    let Some(cache) = FileSystemUpdateCacheRepository.get() else {
        return false;
    };
    cache.seen_whats_new_version.as_deref() != Some(current_version)
}

/// Python `mark_version_as_seen`: stamp the current version as shown, keeping
/// the stored latest version and dismissal. Python leaves the write's
/// `OSError` for the caller's task to log; the banner mount does not handle
/// it, so the log lives here.
pub fn mark_seen(current_version: &str) {
    let repository = FileSystemUpdateCacheRepository;
    if let Err(err) = repository.modify(&mut |cache| {
        Some(match cache {
            Some(cache) => UpdateCache {
                seen_whats_new_version: Some(current_version.to_owned()),
                ..cache
            },
            None => UpdateCache {
                latest_version: current_version.to_owned(),
                stored_at_timestamp: now_unix(),
                seen_whats_new_version: Some(current_version.to_owned()),
                dismissed_version: None,
                // Not an update check: no manager answered, so no stamp.
                source: None,
                source_stored_at: None,
            },
        })
    }) {
        tracing::debug!(%err, "Failed to mark what's-new version as seen");
    }
}
