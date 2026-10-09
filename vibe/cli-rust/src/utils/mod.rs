//! Pure-logic helpers not tied to rendering.

/// True when driven by the e2e replay harness (`VIBE_REPLAY_FIXTURE`).
pub fn is_replaying() -> bool {
    std::env::var_os("VIBE_REPLAY_FIXTURE").is_some()
}

pub fn replay_now_ms() -> Option<u64> {
    if !is_replaying() {
        return None;
    }
    std::env::var("VIBE_REPLAY_NOW_MS").ok()?.parse().ok()
}

/// Unix seconds now, or 0 when the clock is before the epoch.
pub fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0)
}

pub mod cache_store;
pub mod clean;
pub mod datetime;
pub mod file_index;
pub mod file_match;
pub mod fuzzy;
pub mod graphemes;
pub mod greeting_cache;
pub mod history_manager;
pub mod history_persist;
pub mod input_edit;
pub mod paths;
pub mod scroll;
pub mod startup_cache;
pub mod text;
pub mod text_diff;
pub mod tls;
pub mod transcript_cache;
pub mod whats_new_cache;
