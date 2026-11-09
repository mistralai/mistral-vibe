//! The last row the user sees above the queued prompts and the pinned banner.

use serde_json::Value;

use super::{FiredLoop, Transcript};
use crate::server::{HistoryEntry, MessageEntry};

impl Transcript {
    /// The last shown row above the queued prompts and the pinned banner.
    pub fn last_shown_row(&self) -> Option<&Value> {
        self.entries
            .iter()
            .enumerate()
            .rev()
            .filter(|(_, stored)| !stored.pending && !stored.pinned_bottom)
            .find(|(index, _)| !self.is_hidden(*index))
            .map(|(_, stored)| &stored.raw)
    }

    /// The settled prompt at `index`: whether it is packed against the prompt before it, and the loop that sent it.
    pub fn settled_prompt(
        &self,
        index: usize,
    ) -> Option<(&MessageEntry, bool, Option<&FiredLoop>)> {
        let stored = self.entries.get(index).filter(|stored| !stored.pending)?;
        match &stored.typed {
            HistoryEntry::Message(message) if message.role == "user" => {
                Some((message, stored.follows_user, stored.fired_loop.as_ref()))
            }
            _ => None,
        }
    }
}
