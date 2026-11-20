//! Whether a prompt is still unanswered, so an interrupt can take its turn back.

use super::Transcript;
use crate::server::HistoryEntry;

impl Transcript {
    /// Id of the last rewindable prompt when no assistant text or tool call follows it.
    pub fn unanswered_prompt(&self) -> Option<String> {
        let (index, id, _) = self.user_messages().pop()?;
        (!self.answered_after(index)).then_some(id)
    }

    /// The prompt that opened `turn_id`, when it is the turn's only prompt and nothing answers it.
    pub fn unanswered_turn_prompt(&self, turn_id: &str) -> Option<String> {
        let prompts = self.user_messages();
        let mut own = prompts
            .iter()
            .filter(|(index, _, _)| self.entries[*index].raw["turnId"].as_str() == Some(turn_id));
        let (index, id, _) = own.next()?;
        let last = prompts.last().map(|(last, _, _)| *last);
        (own.next().is_none() && last == Some(*index) && !self.answered_after(*index))
            .then(|| id.clone())
    }

    fn answered_after(&self, index: usize) -> bool {
        self.entries[index + 1..]
            .iter()
            .any(|stored| !stored.local && answers(&stored.typed))
    }
}

/// Visible turn output; reasoning alone does not answer a prompt.
fn answers(entry: &HistoryEntry) -> bool {
    match entry {
        HistoryEntry::Message(message) => {
            message.role == "assistant" && !message.text("").trim().is_empty()
        }
        HistoryEntry::Effect(_) | HistoryEntry::Callback(_) => true,
        _ => false,
    }
}
