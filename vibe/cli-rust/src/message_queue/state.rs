//! Desired and in-flight state for merged queue replacements.

use super::QueueController;

impl QueueController {
    pub(super) fn revise_group(&mut self, server_message_id: &str) -> u64 {
        self.next_revision = self.next_revision.wrapping_add(1);
        let revision = self.next_revision;
        for item in &mut self.items {
            if item.server_message_id == server_message_id {
                item.revision = revision;
            }
        }
        revision
    }

    pub(super) fn begin_replace(&mut self, server_message_id: &str) -> Option<u64> {
        if self.replacement_in_flight(server_message_id) {
            return None;
        }
        let revision = self.group_revision(server_message_id)?;
        for item in &mut self.items {
            if item.server_message_id == server_message_id {
                item.replacing = true;
            }
        }
        Some(revision)
    }

    pub(super) fn finish_replace(&mut self, server_message_id: &str) {
        for item in &mut self.items {
            if item.server_message_id == server_message_id {
                item.replacing = false;
            }
        }
    }

    pub(super) fn replacement_in_flight(&self, server_message_id: &str) -> bool {
        self.items
            .iter()
            .any(|item| item.server_message_id == server_message_id && item.replacing)
    }

    pub(super) fn any_replacement_in_flight(&self) -> bool {
        self.items.iter().any(|item| item.replacing)
    }

    pub(super) fn group_revision(&self, server_message_id: &str) -> Option<u64> {
        self.items
            .iter()
            .find(|item| item.server_message_id == server_message_id)
            .map(|item| item.revision)
    }
}
