//! One cached child transcript and its view strings (Python `_Transcript`).

use std::time::Duration;

use serde_json::Value;

use crate::transcript::Transcript;

pub const MAX_CACHED_SUBAGENT_TRANSCRIPTS: usize = 5;
/// Python `HISTORY_RESUME_TAIL_MESSAGES` (`windowing/state.py`).
pub const HISTORY_RESUME_TAIL_MESSAGES: u64 = 20;
pub const HISTORY_PAGE_LIMIT: u64 = 500;
/// Python drains transcript refreshes on a 50 ms debounce.
pub const REFRESH_DEBOUNCE: Duration = Duration::from_millis(50);

pub const LOADING_PLACEHOLDER: &str = "Loading subagent transcript…";
pub const EMPTY_PLACEHOLDER: &str = "No transcript yet.";
pub const ERROR_PLACEHOLDER: &str =
    "This subagent is closed. Its transcript is no longer available.";
pub const READ_ONLY_MESSAGE: &str = "You can't interact with a subagent directly. Return to Main conversation and ask the main agent to stop it.";
pub const READY_MESSAGE: &str = "This subagent is ready for a new instruction. Return to Main conversation and ask the main agent to give it a new goal or stop it.";

/// Python `UserMessageSeverity` for locally appended child-transcript messages.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Info,
    Error,
}

/// Placeholder shown before the first history lands, or after a failed fetch.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum FetchState {
    Loading,
    Error,
}

/// Fetch answers, applied on the main thread.
pub enum Event {
    Fetched {
        session_id: String,
        history: Vec<Value>,
        complete: bool,
    },
    Failed {
        session_id: String,
    },
}

/// One cached child transcript (Python `_Transcript`).
pub struct ChildTranscript {
    pub transcript: Transcript,
    /// The child's own render cache; swapped in with the transcript so the
    /// two revision streams never collide on cached heights.
    pub cache: crate::utils::transcript_cache::TranscriptCache,
    /// Document height of the last overflowing child frame (`View::last_total`).
    pub last_total: u16,
    pub history: Option<Vec<Value>>,
    pub history_complete: bool,
    pub local_user_messages: Vec<(String, Severity)>,
    pub fetch_state: Option<FetchState>,
}

impl Default for ChildTranscript {
    fn default() -> Self {
        Self {
            transcript: Transcript::default(),
            cache: crate::utils::transcript_cache::TranscriptCache::default(),
            last_total: 0,
            history: None,
            history_complete: false,
            local_user_messages: Vec::new(),
            fetch_state: Some(FetchState::Loading),
        }
    }
}

impl ChildTranscript {
    /// The placeholder row the empty child view shows, if any.
    pub fn placeholder(&self) -> Option<&'static str> {
        self.placeholder_with(self.transcript.is_empty())
    }

    /// Same, with the empty check supplied by the caller: during a frame the
    /// child's transcript is swapped into `View`, so `self.transcript` holds
    /// the main conversation and cannot answer it.
    pub fn placeholder_with(&self, rendered_is_empty: bool) -> Option<&'static str> {
        match self.fetch_state {
            Some(FetchState::Loading) => Some(LOADING_PLACEHOLDER),
            Some(FetchState::Error) if self.history.is_none() => Some(ERROR_PLACEHOLDER),
            _ if rendered_is_empty => Some(EMPTY_PLACEHOLDER),
            _ => None,
        }
    }
}
