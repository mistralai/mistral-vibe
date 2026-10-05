//! Sub-agents view state (Python `SubagentList` + `app._viewed_subagent_id`).

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

/// Python `app_server.child_sessions` + the subagent list and view state.
pub struct Subagents {
    /// Every child session, sorted by `(createdAt, id)` (Python `_replace_child_session`).
    pub sessions: Vec<PublicChildSession>,
    /// Python `config.show_subagent_status_list`.
    pub status_list_enabled: bool,
    /// Python `_viewed_subagent_id`: the child transcript shown, if any.
    pub viewed_subagent_id: Option<String>,
    /// The main session's context budget while a child view masks it
    /// (Python re-reads `runtime.stats`; the client tracks the latest).
    pub main_tokens: (u64, u64),
    pub list: ListState,
    /// Python `SubagentTranscripts`: the bounded child-transcript cache.
    pub transcripts: SubagentTranscripts,
    /// Python `_subagent_refresh_requested`: a fetch was asked for while the
    /// drain was already sleeping or reading.
    pub refresh_requested: bool,
    /// The 50ms sleep deadline. Later updates must not push it forward.
    pub refresh_at: Option<Instant>,
    /// Python awaits one history read; a second read waits for it to land.
    pub refresh_in_flight: bool,
    /// Fetch answers, applied on the main thread.
    pub tx: Option<mpsc::Sender<Event>>,
}

impl Subagents {
    /// Python `_replace_child_session`: upsert, keeping `(createdAt, id)` order.
    pub fn replace_child_session(&mut self, child: PublicChildSession) {
        if let Some(existing) = self.sessions.iter_mut().find(|s| s.id == child.id) {
            *existing = child;
            return;
        }
        self.sessions.push(child);
        self.sessions
            .sort_by(|a, b| (a.created_at, &a.id).cmp(&(b.created_at, &b.id)));
    }

    /// Seed the list from a snapshot (Python reconciles it into per-child updates).
    pub fn seed_snapshot(&mut self, children: Vec<PublicChildSession>) {
        self.sessions = children;
        self.sessions
            .sort_by(|a, b| (a.created_at, &a.id).cmp(&(b.created_at, &b.id)));
    }

    /// Python `_viewed_subagent`: the viewed child's session, if still present.
    pub fn viewed_child(&self) -> Option<&PublicChildSession> {
        let id = self.viewed_subagent_id.as_deref()?;
        self.sessions.iter().find(|session| session.id == id)
    }

    /// Python `_refresh_subagent_list` data half: the rows the list renders.
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
        // Python's `show_subagent_status_list` defaults to true.
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
