//! Folding one entry or group open or shut, the same in the conversation and the Sessions page preview.

use std::collections::HashSet;

use super::grouping::KEY_PREFIX;
use super::{Transcript, TURN_DETAILS_PREFIX};

impl Transcript {
    /// Fold `id` open or shut in `expanded`; `None` when it does not fold, else whether it opened.
    pub fn toggle_fold(&self, expanded: &mut HashSet<String>, id: &str) -> Option<bool> {
        if !self.is_expandable(id) {
            return None;
        }
        let lone = self.group_of(id).is_none();
        let reveal = !expanded.remove(id);
        // A lone call carries its fold into the group a later call may open under its id.
        if lone && !id.starts_with(TURN_DETAILS_PREFIX) && !id.starts_with(KEY_PREFIX) {
            let group_key = format!("{KEY_PREFIX}{id}");
            expanded.remove(&group_key);
            if reveal {
                expanded.insert(group_key);
            }
        }
        if reveal {
            expanded.insert(id.to_owned());
        }
        Some(reveal)
    }
}
