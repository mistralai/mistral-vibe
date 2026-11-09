//! Settling rows the server left in progress once their turn is over.

use serde_json::{json, Value};

use super::Transcript;
use crate::server::{HistoryEntry, PublicSessionState};

impl Transcript {
    /// Settle the active reasoning row when response generation moves on.
    pub fn settle_reasoning(&mut self) {
        let Some(index) = self.entries.iter().rposition(|stored| {
            matches!(&stored.typed, HistoryEntry::Reasoning(reasoning) if reasoning.generation_status.as_deref() == Some("in_progress"))
        }) else {
            return;
        };
        // Persist onto raw so a later text-append `update` does not revive Thinking.
        self.settle_at(index, |_| {});
    }

    /// Settle every compaction still in progress outside the `running` turn. An
    /// interrupt stops a compaction without a terminal checkpoint, so it would
    /// otherwise pulse on; a later server update still overrides this.
    pub fn settle_compactions(&mut self, running: Option<&str>) {
        let open: Vec<usize> = (0..self.entries.len())
            .filter(|&index| {
                let stored = &self.entries[index];
                matches!(&stored.typed, HistoryEntry::Checkpoint(checkpoint) if checkpoint.kind == "compaction" && checkpoint.generation_status.as_deref() == Some("in_progress"))
                    && (running.is_none() || stored.raw["turnId"].as_str() != running)
            })
            .collect();
        for index in open {
            self.settle_at(index, |raw| {
                raw["message"] = Value::String("Context compaction interrupted".into());
                if !raw["details"].is_object() {
                    raw["details"] = json!({});
                }
                raw["details"]["error"] = json!({"code": "interrupted"});
            });
        }
    }

    /// After a rebuild, settle the compactions no running turn of `state` owns.
    pub(super) fn settle_rebuilt_compactions(&mut self, state: &PublicSessionState) {
        let Some(turns) = &state.turns else {
            return;
        };
        let running = turns
            .last()
            .filter(|turn| turn["status"].as_str() == Some("in_progress"))
            .and_then(|turn| turn["id"].as_str());
        self.settle_compactions(running);
    }

    fn settle_at(&mut self, index: usize, patch: impl FnOnce(&mut Value)) {
        let stored = &mut self.entries[index];
        stored.raw["generationStatus"] = Value::String("completed".into());
        patch(&mut stored.raw);
        stored.typed = HistoryEntry::from_value(&stored.raw);
        self.next_rev += 1;
        stored.rev = self.next_rev;
    }
}
