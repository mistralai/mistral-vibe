//! Older server history pages prepended above the loaded transcript.

use serde_json::Value;

use super::{entry_id, Transcript};
use crate::server::PublicSessionState;

/// Where `session/history/list` resumes paging backward (Python `history_before_cursor`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HistoryCursor {
    pub session_id: String,
    pub before: String,
}

impl HistoryCursor {
    pub fn of(state: &PublicSessionState) -> Option<Self> {
        Some(Self {
            session_id: state.session.id.clone(),
            before: state.history_before_cursor.clone()?,
        })
    }
}

impl Transcript {
    pub fn history_before_cursor(&self) -> Option<&HistoryCursor> {
        self.history_before_cursor.as_ref()
    }

    /// Prepend the page answering `cursor`; `None` once this transcript pages elsewhere.
    pub fn prepend_history(
        &mut self,
        cursor: &HistoryCursor,
        items: Vec<Value>,
        next: Option<String>,
    ) -> Option<usize> {
        if self.history_before_cursor.as_ref() != Some(cursor) {
            return None;
        }
        let mut page = Transcript {
            show_reasoning: self.show_reasoning,
            next_rev: self.next_rev,
            ..Transcript::default()
        };
        for raw in items {
            if entry_id(&raw).is_some_and(|id| !self.indices.contains_key(&id)) {
                page.insert(raw, true);
            }
        }
        let added = page.entries.len();
        // An empty page cannot move the cursor, so paging ends there.
        self.history_before_cursor = next.filter(|_| added > 0).map(|before| HistoryCursor {
            session_id: cursor.session_id.clone(),
            before,
        });
        if added == 0 {
            return Some(0);
        }
        self.next_rev = page.next_rev;
        self.hidden.extend(page.hidden);
        page.entries.append(&mut self.entries);
        self.entries = page.entries;
        for (position, stored) in self.entries.iter().enumerate() {
            self.indices.insert(stored.id.clone(), position);
        }
        // The seam can regroup the first shifted rows, so they measure again.
        for position in added..self.entries.len() {
            self.next_rev += 1;
            self.entries[position].rev = self.next_rev;
            if self.splits_tool_group(position) {
                break;
            }
        }
        Some(added)
    }
}
