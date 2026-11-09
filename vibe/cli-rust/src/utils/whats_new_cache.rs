//! The what's-new seen-version gate over the shared `[update_cache]` section
//! of `cache.toml`; the update checks
//! that seed the cache live in `update_notifier`.

use super::now_unix;
use crate::update_notifier::cache::{
    FileSystemUpdateCacheRepository, UpdateCache, UpdateCacheRepository,
};

/// What the cache says the user last saw.
#[derive(Debug, PartialEq, Eq)]
pub enum LastSeen {
    /// No cache yet (a fresh install): show nothing, stamp nothing.
    NoCache,
    /// A cache whose seen version is absent when no banner was ever stamped.
    Seen(Option<String>),
}

pub fn last_seen() -> LastSeen {
    match FileSystemUpdateCacheRepository.get() {
        Some(cache) => LastSeen::Seen(cache.seen_whats_new_version),
        None => LastSeen::NoCache,
    }
}

/// Whether a cache exists and its seen version differs from `current_version`.
pub fn should_show(current_version: &str) -> bool {
    matches!(last_seen(), LastSeen::Seen(seen) if seen.as_deref() != Some(current_version))
}

/// Stamp the current version as shown, keeping the stored latest version and
/// dismissal. The banner mount does not handle a failed write, so the log
/// lives here.
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
