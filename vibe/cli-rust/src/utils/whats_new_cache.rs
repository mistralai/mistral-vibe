//! The what's-new seen-version gate, `[whats_new] seen_version` in `cache.toml`.
//!
//! Python stores `seen_whats_new_version` in its `[update_cache]` section, which
//! its update-notifier seeds from update checks; the e2e whats-new scenario pins
//! one shared `VIBE_HOME` for both clients, where the first capture's write would
//! close the second's gate, so the Rust client keeps its own section instead.

use super::cache_store;

const SECTION: &str = "whats_new";
const KEY: &str = "seen_version";

/// Python `should_show_whats_new`: show when the stored seen version differs from
/// the current one. An absent section is a first run: stamp the current version
/// (no show this run) and let a later version change open the gate. Divergence:
/// Python's first update check seeds `seen_whats_new_version=None`, so a fresh
/// install shows what's-new on its next launch; this client stays quiet until
/// the version actually changes.
pub fn should_show(current_version: &str) -> bool {
    match cache_store::read_string(SECTION, KEY) {
        None => {
            cache_store::write_string(SECTION, KEY, current_version);
            false
        }
        Some(seen) => seen != current_version,
    }
}

/// Python `mark_version_as_seen`: stamp the current version as shown.
pub fn mark_seen(current_version: &str) {
    cache_store::write_string(SECTION, KEY, current_version);
}
