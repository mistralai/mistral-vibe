//! The last row the user sees above the queued prompts and the pinned banner.

use serde_json::Value;

use super::Transcript;

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
}
