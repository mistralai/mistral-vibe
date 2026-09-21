//! Transcript reducer: raw JSON and its typed render projection, kept in sync by id.

pub(crate) mod grouping;
pub(crate) mod local;
pub(crate) mod patch;
mod subagent;

use std::collections::{HashMap, HashSet};

use crate::server::HistoryEntry;
use serde_json::Value;

use grouping::is_hidden_hook_notice;
pub use grouping::{effect_kind, groups_with_tools, outcome, Group, Outcome};
use patch::appended_text;
pub use patch::apply_json_patch;

#[derive(Default)]
pub struct Transcript {
    entries: Vec<StoredEntry>,
    indices: HashMap<String, usize>,
    /// Entries kept in store but not rendered: retry-continuation sources.
    hidden: HashSet<String>,
    next_rev: u64,
    pub last_event_id: u64,
    /// Whether reasoning entries are shown (Python `config.show_thinking_nodes`).
    show_reasoning: bool,
    /// Whether the live trailing tool group still spins (Python keeps
    /// `current_tool_group` open across a successful turn, and finalizes it
    /// only on a non-grouping entry, a failed/interrupted turn, or a rebuild).
    live_tail_open: bool,
}

pub struct Snapshot {
    entries: Vec<Value>,
    hidden: HashSet<String>,
    last_event_id: u64,
}

struct StoredEntry {
    id: String,
    raw: Value,
    typed: HistoryEntry,
    rev: u64,
    local: bool,
    pending: bool,
    follows_user: bool,
    followed_by_user: bool,
    historical: bool,
    /// Client-pinned to the document bottom (Python mounts the what's-new
    /// message after the messages area, so later entries slot in above it).
    pinned_bottom: bool,
    /// Whether history widgets existed at mount time (Python `after-history`).
    after_history: bool,
    /// The latest `/state/outputText` append this entry received (Python's
    /// stream widget holds the last patch's delta, not the merged text).
    stream_delta: Option<String>,
    /// Completed subagent response carried by the following harness notification.
    attached_output: Option<String>,
}

/// A borrowed renderable entry: its id, height-cache revision, and typed variant.
pub struct TranscriptEntry<'a> {
    pub index: usize,
    pub id: &'a str,
    pub rev: u64,
    pub local: bool,
    /// Aggregate tool/reasoning group this entry belongs to.
    pub group: Option<Group<'a>>,
    /// Queued prompt the server has accepted but not promoted to a turn yet.
    pub pending: bool,
    /// This prompt is visually packed against the previous user prompt.
    pub follows_user: bool,
    /// The next user prompt is visually packed against this prompt.
    pub followed_by_user: bool,
    /// Opens the queued run, so it paints the `» Queued` header above itself.
    pub queue_header: bool,
    /// What's-new body mounted below history (Python `.whats-new-message.after-history`).
    pub after_history: bool,
    /// The latest text appended to a running effect's `state.outputText`.
    pub stream_delta: Option<&'a str>,
    /// Completed subagent response carried by the following harness notification.
    pub attached_output: Option<&'a str>,
    pub entry: &'a HistoryEntry,
}

impl Transcript {
    pub fn snapshot(&self) -> Snapshot {
        Snapshot {
            entries: self.entries.iter().map(|entry| entry.raw.clone()).collect(),
            hidden: self.hidden.clone(),
            last_event_id: self.last_event_id,
        }
    }

    pub fn restore(&mut self, snapshot: Snapshot) {
        self.entries.clear();
        self.indices.clear();
        self.hidden = snapshot.hidden;
        self.next_rev += 1;
        self.last_event_id = snapshot.last_event_id;
        for entry in snapshot.entries {
            self.insert(entry, true);
        }
    }

    /// Discard all visible entries for an optimistic `/clear`.
    pub fn clear(&mut self) {
        self.entries.clear();
        self.indices.clear();
        self.next_rev += 1;
        self.live_tail_open = false;
    }

    /// Python `stop_current_tool_call`: a failed or interrupted turn settles
    /// the open live group; a successful one leaves it spinning.
    pub fn finalize_tool_group(&mut self) {
        self.live_tail_open = false;
    }

    /// Most recent non-empty assistant text, for `/copy`. Skips hidden entries
    /// (an active retry continuation's source row would otherwise be copied
    /// instead of the merged answer it feeds).
    pub fn last_assistant_message(&self) -> Option<String> {
        self.entries
            .iter()
            .enumerate()
            .rev()
            .find_map(|(index, stored)| {
                if self.is_hidden(index) {
                    return None;
                }
                let HistoryEntry::Message(message) = &stored.typed else {
                    return None;
                };
                if message.role != "assistant" {
                    return None;
                }
                let text = message_text(message);
                (!text.trim().is_empty()).then_some(text)
            })
    }

    /// Rewindable user messages as `(entry index, entry id, text)`: server-owned
    /// user entries only, mirroring Python's `_get_user_message_widgets`, which
    /// skips queued prompts and slash-command echoes.
    pub fn user_messages(&self) -> Vec<(usize, String, String)> {
        self.entries
            .iter()
            .enumerate()
            .filter(|(index, stored)| !stored.local && !self.is_hidden(*index))
            .filter_map(|(index, stored)| {
                let HistoryEntry::Message(message) = &stored.typed else {
                    return None;
                };
                (message.role == "user").then(|| (index, stored.id.clone(), message_text(message)))
            })
            .collect()
    }

    /// Replace state from a full `PublicSessionState` (snapshot / session start).
    /// A rebuild is Python `build_history_widgets`: it settles every group,
    /// including the trailing one (the post-loop `current_group.finalize()`).
    pub fn load_snapshot(&mut self, state: &crate::server::PublicSessionState) {
        let local_entries = local::preserve(self);
        self.entries.clear();
        self.indices.clear();
        self.hidden.clear();
        self.next_rev += 1;
        self.live_tail_open = false;
        self.last_event_id = state.event_id;
        if let Some(history) = &state.history {
            for entry in history {
                self.insert(entry.clone(), true);
            }
        }
        local::restore(self, local_entries);
    }

    /// A live same-session snapshot (ADR 0009): retain the contiguous prefix
    /// already loaded before the snapshot's history page (Python
    /// `_merge_snapshot_suffix`) instead of discarding it, so a bounded page
    /// never drops older rows a pending continuation still needs.
    pub fn load_live_snapshot(&mut self, state: &crate::server::PublicSessionState) {
        let local_entries = local::preserve(self);
        let previous: Vec<Value> = self
            .entries
            .iter()
            .filter(|entry| !entry.local)
            .map(|entry| entry.raw.clone())
            .collect();
        let history = match state.history.as_ref() {
            // Python keeps the loaded history when the snapshot carries none.
            None => previous,
            Some(current) => {
                merge_snapshot_suffix(&previous, current).unwrap_or_else(|| current.clone())
            }
        };
        self.entries.clear();
        self.indices.clear();
        self.hidden.clear();
        self.next_rev += 1;
        self.last_event_id = state.event_id;
        for entry in history {
            self.insert(entry, true);
        }
        local::restore(self, local_entries);
    }

    /// `history/entryAdded`: Python `_handle_entry_added` finalizes the open
    /// group when the entry cannot join it, and reopens one when it can.
    pub fn add(&mut self, params: &Value) {
        if let Some(entry) = params.get("entry") {
            self.live_tail_open = self.insert(entry.clone(), false);
        }
        self.bump_event(params);
    }

    /// `history/entryUpdated` — apply the JSON-Patch ops to the stored entry.
    pub fn update(&mut self, params: &Value) {
        let Some(id) = params.get("entryId").and_then(Value::as_str) else {
            return;
        };
        let Some(&index) = self.indices.get(id) else {
            return;
        };
        let stored = &mut self.entries[index];
        if let Some(ops) = params.get("patch").and_then(Value::as_array) {
            apply_json_patch(&mut stored.raw, ops);
            // Python's stream line shows the patch's own append (`_appended_text`),
            // and a patch without one leaves the previous delta in place.
            let delta = appended_text(ops, "/state/outputText");
            if !delta.is_empty() {
                stored.stream_delta = Some(delta);
            }
        }
        stored.typed = HistoryEntry::from_value(&stored.raw);
        self.next_rev += 1;
        stored.rev = self.next_rev;
        self.bump_event(params);
    }

    /// Insert one entry, returning whether it can join a tool group
    /// (Python `entry_keeps_tool_group`).
    fn insert(&mut self, mut raw: Value, historical: bool) -> bool {
        let Some(id) = entry_id(&raw) else {
            return false;
        };
        if let Some(&index) = self.indices.get(&id) {
            let existing = &self.entries[index];
            if existing.local {
                local::preserve_file_image_links(&existing.raw, &mut raw);
            }
        }
        self.next_rev += 1;
        let server_projection = HistoryEntry::from_value(&raw);
        let display = local::user_projection_for_echo(self, &id, &raw, &server_projection);
        let (typed, follows_user, followed_by_user) = display.unwrap_or_else(|| {
            (
                server_projection,
                raw.get("followsUser")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                raw.get("followedByUser")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            )
        });
        let mut groups = groups_with_tools(&typed);
        let attached_output = self
            .indices
            .get(&id)
            .and_then(|&index| self.entries[index].attached_output.clone());
        let stored = StoredEntry {
            id: id.clone(),
            typed,
            local: raw.get("local").and_then(Value::as_bool).unwrap_or(false),
            pending: raw.get("pending").and_then(Value::as_bool).unwrap_or(false),
            follows_user,
            followed_by_user,
            historical: historical
                || raw
                    .get("historical")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            pinned_bottom: raw
                .get("pinnedBottom")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            after_history: raw
                .get("afterHistory")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            stream_delta: None,
            attached_output,
            raw,
            rev: self.next_rev,
        };
        if let Some(&index) = self.indices.get(&id) {
            self.entries[index] = stored;
            return groups;
        }
        // The queued run is pinned to the bottom (Python `QueueController.pin_target`),
        // so content arriving for the running turn slots in above it; a bottom-pinned
        // banner stays below even the queue, like Python's after-messages widgets.
        // A pre-tool hook notice slots in ahead of its paired tool entry, whose
        // anchor Python mounts the container before.
        let at = grouping::pre_tool_anchor(&stored.typed)
            .and_then(|anchor| self.indices.get(&anchor).copied())
            .unwrap_or_else(|| match (stored.pinned_bottom, stored.pending) {
                (true, _) => self.entries.len(),
                (false, true) => self.first_pinned(),
                (false, false) => self.first_pending().min(self.first_pinned()),
            });
        if let Some(notification) = subagent::notification(&stored.typed) {
            let previous = [
                subagent::EffectMatch::ChildSession,
                subagent::EffectMatch::AgentName,
            ]
            .into_iter()
            .find_map(|wanted| {
                self.entries[..at].iter().rposition(|entry| {
                    entry.attached_output.is_none()
                        && subagent::effect_match(&entry.raw, &entry.typed, &notification)
                            == Some(wanted)
                })
            });
            if let Some(previous) = previous {
                self.next_rev += 1;
                self.entries[previous].rev = self.next_rev;
                self.entries[previous].attached_output = Some(notification.output);
                self.hidden.insert(id.clone());
                groups = true;
            }
        }
        self.entries.insert(at, stored);
        self.indices.insert(id, at);
        self.reindex(at + 1);
        groups
    }

    fn first_pending(&self) -> usize {
        self.entries
            .iter()
            .position(|entry| entry.pending)
            .unwrap_or(self.entries.len())
    }

    fn first_pinned(&self) -> usize {
        self.entries
            .iter()
            .position(|entry| entry.pinned_bottom)
            .unwrap_or(self.entries.len())
    }

    /// Whether the server-owned history is non-empty (Python tracks its mounted
    /// history widgets; the what's-new body adds a top margin only when they exist).
    pub fn has_server_entries(&self) -> bool {
        self.entries.iter().any(|entry| !entry.local)
    }

    /// Renumber the entries from `at`: they moved, so their cached heights, which
    /// are keyed by position and revision, must not be reused.
    fn reindex(&mut self, at: usize) {
        for position in at..self.entries.len() {
            self.next_rev += 1;
            let stored = &mut self.entries[position];
            stored.rev = self.next_rev;
            let id = stored.id.clone();
            self.indices.insert(id, position);
        }
    }

    /// Rewrite a client-owned entry in place and re-derive its typed projection.
    pub fn patch_local(&mut self, id: &str, edit: impl FnOnce(&mut Value)) {
        let Some(&index) = self.indices.get(id) else {
            return;
        };
        let mut raw = std::mem::take(&mut self.entries[index].raw);
        edit(&mut raw);
        self.insert(raw, false);
    }

    /// Drop an entry, renumbering the indices and revisions that shift with it.
    pub fn remove(&mut self, id: &str) {
        let Some(index) = self.indices.remove(id) else {
            return;
        };
        self.entries.remove(index);
        self.reindex(index);
        self.next_rev += 1;
    }

    /// Keep an entry in store but out of the render (retry-continuation source).
    pub fn hide(&mut self, id: &str) {
        self.hidden.insert(id.to_owned());
        self.next_rev += 1;
    }

    /// The raw `content` array of an entry; the retry merge freezes it as its base.
    pub fn entry_content(&self, id: &str) -> Option<Value> {
        let index = *self.indices.get(id)?;
        Some(self.entries[index].raw.get("content")?.clone())
    }

    /// Merge the hidden retried entry into the interrupted assistant row
    /// (Python reuses the widget and appends): recompute the row as its frozen
    /// base with the continuation's text spliced into the last text block —
    /// Python's `append_content` writes into the same stream, no separator —
    /// and the continuation's status applied.
    pub fn merge_continuation(&mut self, dst: &str, src: &str, base: &Value) {
        let (Some(&src_index), Some(&dst_index)) = (self.indices.get(src), self.indices.get(dst))
        else {
            return;
        };
        // Python `PublicMessageEntry.text`: the continuation's text blocks joined.
        let appended: Vec<String> = self.entries[src_index]
            .raw
            .get("content")
            .and_then(Value::as_array)
            .map(|blocks| {
                blocks
                    .iter()
                    .filter(|block| block.get("type").and_then(Value::as_str) == Some("text"))
                    .filter_map(|block| block.get("text").and_then(Value::as_str))
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default();
        let status = self.entries[src_index].raw.get("generationStatus").cloned();
        let mut content = base.clone();
        if !content.is_array() {
            content = Value::Array(Vec::new());
        }
        if let Some(blocks) = content.as_array_mut() {
            let last_text = blocks
                .iter_mut()
                .rev()
                .find(|block| block.get("type").and_then(Value::as_str) == Some("text"));
            match last_text {
                // Python writes straight into the existing stream: no separator.
                Some(block) => {
                    let merged = format!(
                        "{}{}",
                        block
                            .get("text")
                            .and_then(Value::as_str)
                            .unwrap_or_default(),
                        appended.join("\n\n")
                    );
                    block["text"] = Value::String(merged);
                }
                None => blocks.push(serde_json::json!({
                    "type": "text",
                    "text": appended.join("\n\n"),
                })),
            }
        }
        let stored = &mut self.entries[dst_index];
        stored.raw["content"] = content;
        if let Some(status) = status {
            stored.raw["generationStatus"] = status;
        }
        stored.typed = HistoryEntry::from_value(&stored.raw);
        self.next_rev += 1;
        stored.rev = self.next_rev;
    }

    fn bump_event(&mut self, params: &Value) {
        if let Some(id) = params.get("eventId").and_then(Value::as_u64) {
            self.last_event_id = id;
        }
    }

    /// Shown entries in order, minus hidden hook notices and hidden reasoning.
    fn rendered(&self) -> impl Iterator<Item = TranscriptEntry<'_>> + '_ {
        self.entries
            .iter()
            .enumerate()
            .filter(|(index, _)| !self.is_hidden(*index))
            .map(|(index, stored)| self.borrow(index, stored))
    }

    fn is_hidden(&self, index: usize) -> bool {
        let stored = &self.entries[index];
        if is_hidden_hook_notice(&stored.typed, &self.entries[..index]) {
            return true;
        }
        if self.hidden.contains(&stored.id) {
            return true;
        }
        !self.show_reasoning && matches!(stored.typed, HistoryEntry::Reasoning(_))
    }

    fn borrow<'a>(&'a self, index: usize, stored: &'a StoredEntry) -> TranscriptEntry<'a> {
        TranscriptEntry {
            index,
            id: &stored.id,
            rev: stored.rev,
            local: stored.local,
            group: self.group_at(index, stored),
            pending: stored.pending,
            follows_user: stored.follows_user,
            followed_by_user: stored.followed_by_user,
            queue_header: stored.pending && !self.previous_is_pending(index),
            after_history: stored.after_history,
            stream_delta: stored.stream_delta.as_deref(),
            attached_output: stored.attached_output.as_deref(),
            entry: &stored.typed,
        }
    }

    /// Python mounts one `QueueHeaderMessage` above the whole queued run, so only
    /// its first prompt carries the header.
    fn previous_is_pending(&self, index: usize) -> bool {
        self.entries[..index]
            .iter()
            .enumerate()
            .rev()
            .find(|(position, _)| !self.is_hidden(*position))
            .is_some_and(|(_, previous)| previous.pending)
    }

    /// Hidden rows stay in `entries` but must not open a new tool group.
    fn splits_tool_group(&self, index: usize) -> bool {
        !self.is_hidden(index) && !groups_with_tools(&self.entries[index].typed)
    }

    /// Describe the visible portion of the consecutive effect/reasoning group.
    fn group_at<'a>(&'a self, index: usize, stored: &'a StoredEntry) -> Option<Group<'a>> {
        if !groups_with_tools(&stored.typed) || (stored.local && !stored.historical) {
            return None;
        }
        let start = self.entries[..index]
            .iter()
            .enumerate()
            .rposition(|(position, _)| self.splits_tool_group(position))
            .map_or(0, |boundary| boundary + 1);
        let end = self.entries[index + 1..]
            .iter()
            .enumerate()
            .position(|(offset, _)| self.splits_tool_group(index + 1 + offset))
            .map_or(self.entries.len(), |offset| index + 1 + offset);
        let visible = self.entries[start..end]
            .iter()
            .enumerate()
            .filter(|(offset, _)| !self.is_hidden(start + offset))
            .map(|(offset, entry)| (start + offset, entry))
            .collect::<Vec<_>>();
        let (first_index, first_entry) = visible.first()?;
        let last_index = visible.last()?.0;
        let mut kinds = Vec::new();
        let mut reasoning = false;
        let mut group_outcome = Outcome::Success;
        for (_, entry) in &visible {
            if let Some(kind) = effect_kind(&entry.typed) {
                if !kinds.contains(&kind) {
                    kinds.push(kind);
                }
            }
            reasoning |= matches!(entry.typed, HistoryEntry::Reasoning(_));
            if let Some(next) = outcome(&entry.typed) {
                group_outcome = next;
            }
        }
        Some(Group {
            key: format!("{}{}", grouping::KEY_PREFIX, first_entry.id),
            first: index == *first_index,
            last: index == last_index,
            finalized: stored.local
                || stored.historical
                || end < self.entries.len()
                || !self.live_tail_open,
            kinds,
            reasoning,
            outcome: group_outcome,
        })
    }

    /// Borrow renderable entries in order without rebuilding their typed values.
    pub fn lines(&self) -> impl Iterator<Item = TranscriptEntry<'_>> + '_ {
        self.rendered()
    }

    /// Monotonic content revision used to invalidate ordered render layouts.
    pub fn revision(&self) -> u64 {
        self.next_rev
    }

    pub fn set_show_reasoning(&mut self, show: bool) {
        if self.show_reasoning != show {
            self.show_reasoning = show;
            self.next_rev += 1;
        }
    }

    /// Settle the active reasoning row when response generation moves on.
    pub fn settle_reasoning(&mut self) {
        let Some(index) = self.entries.iter().rposition(|stored| {
            matches!(&stored.typed, HistoryEntry::Reasoning(reasoning) if reasoning.generation_status.as_deref() == Some("in_progress"))
        }) else {
            return;
        };
        let stored = &mut self.entries[index];
        // Persist onto raw so a later text-append `update` does not revive Thinking.
        stored.raw["generationStatus"] = Value::String("completed".into());
        stored.typed = HistoryEntry::from_value(&stored.raw);
        self.next_rev += 1;
        stored.rev = self.next_rev;
    }

    /// Borrow one entry by its stable position in the append-only transcript.
    pub fn entry(&self, index: usize) -> Option<TranscriptEntry<'_>> {
        let stored = self.entries.get(index)?;
        Some(self.borrow(index, stored))
    }

    /// Whether any entry would render (cheaper than building `lines`). A
    /// bottom-pinned banner does not count: Python's loading area keeps its
    /// fresh-session height while the messages area itself is empty, and the
    /// what's-new widget mounts outside it.
    pub fn is_empty(&self) -> bool {
        !self
            .entries
            .iter()
            .enumerate()
            .any(|(index, stored)| !stored.pinned_bottom && !self.is_hidden(index))
    }

    /// Whether an entry with this id is currently in the transcript.
    pub fn contains(&self, id: &str) -> bool {
        self.indices.contains_key(id)
    }

    /// Whether the entry `id` toggles on click: a tool row or a reasoning trace.
    pub fn is_expandable(&self, id: &str) -> bool {
        if let Some(entry_id) = id.strip_prefix(grouping::KEY_PREFIX) {
            return self
                .indices
                .get(entry_id)
                .and_then(|&index| self.entries.get(index))
                .is_some_and(|stored| groups_with_tools(&stored.typed));
        }
        match self
            .indices
            .get(id)
            .and_then(|&index| self.entries.get(index))
        {
            // An effect whose result Python renders without a fold, or whose
            // section has nothing to unfold, gives a click nothing to reveal.
            Some(stored) => match &stored.typed {
                HistoryEntry::Effect(effect) => {
                    effect.is_collapsible()
                        && (effect.has_body()
                            || stored
                                .attached_output
                                .as_deref()
                                .is_some_and(|output| !output.is_empty()))
                }
                HistoryEntry::Reasoning(_) => true,
                _ => false,
            },
            _ => false,
        }
    }

    /// Whether `id` names a reasoning entry: Python mounts `ReasoningMessage`
    /// with the bulk fold state, both live and on history rebuilds.
    pub fn is_reasoning(&self, id: &str) -> bool {
        self.indices
            .get(id)
            .and_then(|&index| self.entries.get(index))
            .is_some_and(|stored| matches!(stored.typed, HistoryEntry::Reasoning(_)))
    }

    /// Every toggle key Ctrl+O unfolds: each folded group plus each entry a
    /// click can fold (Python `action_toggle_tool` sets every section at once).
    /// The id set does not depend on the group's running state.
    pub fn expandable_ids(&self) -> Vec<String> {
        let mut ids = Vec::new();
        for value in self.lines() {
            if let Some(group) = value.group.as_ref().filter(|group| group.first) {
                ids.push(group.key.clone());
            }
            if self.is_expandable(value.id) {
                ids.push(value.id.to_owned());
            }
        }
        ids
    }

    /// Whether `id` names an assistant message entry, so a text append patch
    /// can feed the narrator's turn summary (Python checks the event's role).
    pub fn is_assistant_message(&self, id: &str) -> bool {
        self.indices
            .get(id)
            .and_then(|&index| self.entries.get(index))
            .is_some_and(
                |stored| matches!(&stored.typed, HistoryEntry::Message(m) if m.role == "assistant"),
            )
    }

    /// `(statusText, terminal)` for the effect `id`: Python steers the loading
    /// label from the call display, then resets it once the result settles.
    pub fn effect_loading_status(&self, id: &str) -> Option<(String, bool)> {
        let stored = self
            .indices
            .get(id)
            .and_then(|&index| self.entries.get(index))?;
        let HistoryEntry::Effect(effect) = &stored.typed else {
            return None;
        };
        Some((effect.status_text().to_owned(), effect.is_terminal()))
    }

    /// Whether a server-owned tool call other than `skip` is still running
    /// (Python resets the loading label only when `tool_calls` is empty).
    pub fn has_live_tool_calls(&self, skip: &str) -> bool {
        self.entries.iter().any(|stored| {
            !stored.local
                && stored.id != skip
                && matches!(&stored.typed, HistoryEntry::Effect(effect) if !effect.is_terminal())
        })
    }
}

/// The text blocks of a message entry, joined like the renderer joins them.
fn message_text(message: &crate::server::MessageEntry) -> String {
    message
        .content
        .iter()
        .filter_map(|content| match content {
            crate::server::MessageContent::Text { text } => Some(text.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn entry_id(entry: &Value) -> Option<String> {
    entry.get("id").and_then(Value::as_str).map(str::to_owned)
}

/// Python `_merge_snapshot_suffix`: the snapshot's page must be a contiguous
/// suffix of the loaded history for the older prefix to be retained; any
/// mismatch replaces the page wholesale. `None` means "no prefix retained".
fn merge_snapshot_suffix(previous: &[Value], current: &[Value]) -> Option<Vec<Value>> {
    if previous.is_empty() || current.is_empty() {
        return None;
    }
    let first = entry_id(current.first()?)?;
    let overlap_start = previous
        .iter()
        .position(|entry| entry_id(entry).as_deref() == Some(first.as_str()))?;
    let overlap = &previous[overlap_start..];
    if overlap.len() > current.len() {
        return None;
    }
    let overlap_ids = overlap.iter().filter_map(entry_id);
    let page_ids = current.iter().filter_map(entry_id).take(overlap.len());
    if !overlap_ids.eq(page_ids) {
        return None;
    }
    let mut merged = previous[..overlap_start].to_vec();
    merged.extend_from_slice(current);
    Some(merged)
}
