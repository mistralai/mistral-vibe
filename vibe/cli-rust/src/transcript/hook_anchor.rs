//! Slots each tool row right below its own pre-tool hook lines.

use super::StoredEntry;
use crate::server::HistoryEntry;

/// The tool call id a pre-tool hook notice names, if `entry` is one.
fn pre_tool_call_id(entry: &HistoryEntry) -> Option<&str> {
    let HistoryEntry::Notice(notice) = entry else {
        return None;
    };
    let detail = notice.detail.as_ref()?;
    if detail.scope.as_deref() != Some("pre_tool") || !detail.kind.as_deref()?.starts_with("hook_")
    {
        return None;
    }
    detail.tool_call_id.as_deref()
}

/// Whether `stored` is the tool entry for `call_id`, by entry id or tool call id.
fn is_tool(stored: &StoredEntry, call_id: &str) -> bool {
    stored.id == call_id
        || matches!(&stored.typed, HistoryEntry::Effect(effect)
            if effect.tool_call_id.as_deref() == Some(call_id))
}

/// Where the current model reply starts: past the last message, so a reused call id never pairs across replies.
fn reply_start(entries: &[StoredEntry]) -> usize {
    entries
        .iter()
        .rposition(|stored| matches!(stored.typed, HistoryEntry::Message(_)))
        .map_or(0, |index| index + 1)
}

/// A pre-tool hook notice arriving after its tool, in the same reply, slots in right above it.
pub(super) fn above_its_tool(entry: &HistoryEntry, entries: &[StoredEntry]) -> Option<usize> {
    let call_id = pre_tool_call_id(entry)?;
    let start = reply_start(entries);
    entries[start..]
        .iter()
        .rposition(|stored| is_tool(stored, call_id))
        .map(|index| start + index)
}

/// A tool arriving after its pre-tool hook notices, as Core runs them all first, slots in right below them.
pub(super) fn below_its_hook_lines(entry: &HistoryEntry, entries: &[StoredEntry]) -> Option<usize> {
    let HistoryEntry::Effect(effect) = entry else {
        return None;
    };
    let call_id = effect.tool_call_id.as_deref()?;
    let start = reply_start(entries);
    entries[start..]
        .iter()
        .rposition(|stored| pre_tool_call_id(&stored.typed) == Some(call_id))
        .map(|index| start + index + 1)
}
