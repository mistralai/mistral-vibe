//! Hold a toggled entry steady and reveal what its expansion uncovered.

use crate::transcript::Transcript;
use crate::utils::scroll::{anchored_row, offset_at, reveal, ScrollAnchor};
use crate::utils::transcript_cache::{LayoutEntry, TranscriptLayout};

/// Document geometry the anchor resolves against.
pub(super) struct Document<'a> {
    pub layout: &'a TranscriptLayout,
    pub transcript: &'a Transcript,
    /// Document row of the first entry, under the banner and its gap.
    pub top: u16,
    pub total: u16,
    pub viewport: u16,
}

/// `(scroll, scroll_target)` holding the entry steady then gliding its expansion into view.
pub(super) fn resolve(anchor: &ScrollAnchor, document: &Document<'_>) -> Option<(u16, u16)> {
    let entries = &document.layout.entries;
    let position = entries
        .binary_search_by_key(&anchor.index, |entry| entry.index)
        .ok()?;
    let entry = &entries[position];
    let group = document.transcript.entry(anchor.index)?.group;
    let key = anchor.key.as_deref();
    // The header follows the gap row, and a group's first member row sits under the group header.
    let header = match &group {
        None => 1,
        Some(group) if group.first && key.is_some_and(|key| key != group.key) => 2,
        Some(group) if group.first => 1,
        Some(_) => 0,
    };
    // A bulk fold leaves no rows for a member cut off at the top: its group header takes the top row.
    let (entry, row) = match group.filter(|group| !group.first && entry.height == 0) {
        Some(group) => (group_start(document, &entries[..position], &group.key)?, -1),
        None => (entry, anchored_row(anchor, header, entry.height)),
    };
    let top = document.top.saturating_add(entry.top);
    let max_scroll = document.total.saturating_sub(document.viewport);
    let scroll = offset_at(document.total, document.viewport, i32::from(top) - row);
    let Some(key) = key.filter(|_| anchor.reveal) else {
        return Some((scroll, scroll));
    };
    let in_block = |index: usize| {
        index == anchor.index
            || document
                .transcript
                .entry(index)
                .and_then(|value| value.group)
                .is_some_and(|group| group.key == key)
    };
    let bottom = entries[position..]
        .iter()
        .take_while(|entry| in_block(entry.index))
        .last()
        .map_or(top, |last| {
            document
                .top
                .saturating_add(last.top)
                .saturating_add(last.height)
        });
    let shown = reveal(
        i32::from(max_scroll - scroll),
        document.viewport,
        top,
        bottom,
    );
    Some((scroll, offset_at(document.total, document.viewport, shown)))
}

/// The layout entry opening group `key`, searched backwards from a member.
fn group_start<'a>(
    document: &Document<'_>,
    before: &'a [LayoutEntry],
    key: &str,
) -> Option<&'a LayoutEntry> {
    before.iter().rev().find(|entry| {
        document
            .transcript
            .entry(entry.index)
            .and_then(|value| value.group)
            .is_some_and(|group| group.first && group.key == key)
    })
}
