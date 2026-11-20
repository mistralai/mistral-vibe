//! Per-frame state an entry renders with: its folds, its turn's `Worked for` group, its queue state.

use std::collections::HashSet;

use super::DetailsView;
use crate::transcript::TranscriptEntry;

/// Per-frame queue state a queued prompt renders with.
#[derive(Clone, Copy, Default)]
pub(in crate::ui::transcript) struct QueueView {
    /// Whether this prompt is the queue-mode highlight.
    pub selected: bool,
    /// Whether the header names the paused queue's shortcuts (held, queue mode closed).
    pub paused: bool,
    /// The queue keys the header shows after its label.
    pub hints: &'static [crate::hints::Hint],
}

#[derive(Clone, Copy)]
pub(in crate::ui::transcript) struct ExpansionView<'a> {
    pub entry: bool,
    pub group: bool,
    /// `/raw`: an agent message shows its Markdown source.
    pub raw: bool,
    /// The turn's `Worked for` group this entry is a step of.
    pub details: Option<DetailsView<'a>>,
}

impl<'a> ExpansionView<'a> {
    /// The fold state of `entry` read from the `expanded` keys, as a step of `details` when it is one.
    pub fn of(
        expanded: &HashSet<String>,
        entry: &TranscriptEntry<'_>,
        details: Option<&'a crate::transcript::TurnDetails>,
        raw: bool,
    ) -> Self {
        let group = entry.group.as_ref();
        Self {
            entry: expanded.contains(entry.id),
            group: group.is_some_and(|group| expanded.contains(&group.key)),
            raw,
            details: details.map(|details| DetailsView {
                details,
                expanded: expanded.contains(&details.key),
            }),
        }
    }

    /// Whether the entry's own rows show: not folded into a tool group or a turn's `Worked for` group.
    pub fn content_visible(&self, entry: &TranscriptEntry<'_>) -> bool {
        (entry.group.is_none() || self.group) && self.details.is_none_or(|view| view.expanded)
    }
}
