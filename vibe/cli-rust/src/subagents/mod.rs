//! Sub-agents view state: the child sessions, the status list, and the viewed child.

pub mod child;
mod display;
pub mod fetch;
pub mod list;
pub mod transcripts;
mod view;

pub use child::{
    Event, FetchState, Severity, HISTORY_PAGE_LIMIT, HISTORY_RESUME_TAIL_MESSAGES,
    MAX_CACHED_SUBAGENT_TRANSCRIPTS, READY_MESSAGE, READ_ONLY_MESSAGE, REFRESH_DEBOUNCE,
};
pub use display::StatusTone;
pub use display::{display_name, is_active, status_label, status_tone, subagent_loading_status};
pub use list::{
    active_batch, focus_first, focus_input, handle_list_key, refresh, select, show_main_chat,
    show_subagent_chat, ListState, MAIN_SESSION_ID, MAX_ROWS,
};
pub use transcripts::{merge_history_tail, SubagentTranscripts};

pub use view::{
    active_transcript, anchor, append_read_only_message, apply_event, refresh_context_progress,
    remember_parent_instruction_from_entry, reset_views, schedule_refresh, start_refresh,
};

use std::time::Instant;

use tokio::sync::mpsc;

use crate::server::PublicChildSession;

/// The child sessions plus the subagent list and view state.
pub struct Subagents {
    /// Every child session, sorted by `(createdAt, id)`.
    pub sessions: Vec<PublicChildSession>,
    /// The `show_subagent_status_list` config value.
    pub status_list_enabled: bool,
    /// The child transcript shown, if any.
    pub viewed_subagent_id: Option<String>,
    /// The main session's context budget while a child view masks it
    /// (the client tracks the latest `runtime.stats`).
    pub main_tokens: (u64, u64),
    pub list: ListState,
    /// The bounded child-transcript cache.
    pub transcripts: SubagentTranscripts,
    /// A fetch was asked for while the drain was already sleeping or reading.
    pub refresh_requested: bool,
    /// The 50ms sleep deadline. Later updates must not push it forward.
    pub refresh_at: Option<Instant>,
    /// One history read at a time; a second read waits for it to land.
    pub refresh_in_flight: bool,
    /// Fetch answers, applied on the main thread.
    pub tx: Option<mpsc::Sender<Event>>,
}

impl Subagents {
    /// Upsert, keeping `(createdAt, id)` order.
    pub fn replace_child_session(&mut self, child: PublicChildSession) {
        if let Some(existing) = self.sessions.iter_mut().find(|s| s.id == child.id) {
            *existing = child;
            return;
        }
        self.sessions.push(child);
        self.sessions
            .sort_by(|a, b| (a.created_at, &a.id).cmp(&(b.created_at, &b.id)));
    }

    /// Seed the list from a snapshot.
    pub fn seed_snapshot(&mut self, children: Vec<PublicChildSession>) {
        self.sessions = children;
        self.sessions
            .sort_by(|a, b| (a.created_at, &a.id).cmp(&(b.created_at, &b.id)));
    }

    /// The viewed child's session, if still present.
    pub fn viewed_child(&self) -> Option<&PublicChildSession> {
        let id = self.viewed_subagent_id.as_deref()?;
        self.sessions.iter().find(|session| session.id == id)
    }

    /// The data half of a list refresh: the rows the list renders.
    pub fn list_rows(&mut self) -> Vec<PublicChildSession> {
        self.list.selected_session_id = self.viewed_subagent_id.clone();
        let sessions = if self.status_list_enabled {
            self.sessions.clone()
        } else {
            Vec::new()
        };
        list::active_batch(
            &mut self.list.known_session_ids,
            &mut self.list.batch_session_ids,
            &sessions,
            self.viewed_subagent_id.as_deref(),
        )
    }
}

impl Default for Subagents {
    fn default() -> Self {
        // The `show_subagent_status_list` config defaults to true.
        Self {
            sessions: Vec::new(),
            status_list_enabled: true,
            viewed_subagent_id: None,
            main_tokens: (0, 0),
            list: ListState::default(),
            transcripts: SubagentTranscripts::default(),
            refresh_requested: false,
            refresh_at: None,
            refresh_in_flight: false,
            tx: None,
        }
    }
}
