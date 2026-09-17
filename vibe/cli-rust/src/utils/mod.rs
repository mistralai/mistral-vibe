//! Pure-logic helpers not tied to rendering.

/// True when driven by the e2e replay harness (`VIBE_REPLAY_FIXTURE`).
pub fn is_replaying() -> bool {
    std::env::var_os("VIBE_REPLAY_FIXTURE").is_some()
}

pub mod cache_store;
pub mod clean;
pub mod file_index;
pub mod file_match;
pub mod fuzzy;
pub mod greeting_cache;
pub mod history_manager;
pub mod input_edit;
pub mod paths;
pub mod scroll;
pub mod startup_cache;
pub mod text;
pub mod transcript_cache;
pub mod whats_new_cache;
