//! Which entries render, and the summary of each packed tool group.

use super::StoredEntry;
use crate::server::{HistoryEntry, NoticeDetail};

pub(crate) const KEY_PREFIX: &str = "tool-group:";

pub struct Group<'a> {
    pub key: String,
    pub first: bool,
    pub last: bool,
    pub finalized: bool,
    /// Each effect kind in first-seen order, with how many calls it made.
    pub kinds: Vec<(&'a str, usize)>,
    pub reasoning: bool,
}

impl Group<'_> {
    /// Kinds sharing a verb merge into one segment: `ran 2 commands and 1 search`.
    pub fn label(&self, running: bool) -> String {
        let mut verbs: Vec<(&str, Vec<String>)> = Vec::new();
        for &(kind, count) in &self.kinds {
            let (verb, noun) = category_terms(kind, count, running);
            let counted = format!("{count} {noun}");
            match verbs.iter_mut().find(|(seen, _)| *seen == verb) {
                Some((_, nouns)) => nouns.push(counted),
                None => verbs.push((verb, vec![counted])),
            }
        }
        let mut labels = verbs
            .into_iter()
            .map(|(verb, nouns)| format!("{verb} {}", join_and(&nouns)))
            .collect::<Vec<_>>();
        if self.reasoning {
            labels.push(if running { "thinking" } else { "thought" }.to_owned());
        }
        let mut label = labels.join(", ");
        if let Some(first) = label.get_mut(0..1) {
            first.make_ascii_uppercase();
        }
        label
    }
}

/// Reasoning, hook notices, and agent effects share one group. A user's `!`
/// command uses the same effect kind as an agent shell call but stays standalone.
pub fn groups_with_tools(entry: &HistoryEntry) -> bool {
    match entry {
        HistoryEntry::Reasoning(_) => true,
        HistoryEntry::Notice(notice) => notice
            .detail
            .as_ref()
            .and_then(|detail| detail.kind.as_deref())
            .is_some_and(|kind| kind.starts_with("hook_")),
        HistoryEntry::Effect(effect) => {
            let Some(detail) = effect.detail.as_ref() else {
                return true;
            };
            detail.tool_name.as_deref() != Some("shell")
                && detail.kind.as_deref() != Some("user_question")
        }
        _ => false,
    }
}

pub fn effect_kind(entry: &HistoryEntry) -> Option<&str> {
    let HistoryEntry::Effect(effect) = entry else {
        return None;
    };
    effect.kind()
}

/// Whether the entry paints no transcript row: a callback (the approval panel
/// is transient), a server notice that only drives side effects (`push_notice`
/// in `ui/transcript/entry/notice.rs` is the dispatch this must mirror), a
/// server checkpoint other than a compaction or model change, or an
/// unrecognized type. Python mounts no widget for them either, so such rows
/// must not break a run of tool calls into consecutive blocks: all calls
/// between them collapse into one block.
pub fn renders_nothing(entry: &HistoryEntry, local: bool) -> bool {
    if groups_with_tools(entry) {
        return false;
    }
    match entry {
        HistoryEntry::Callback(_) | HistoryEntry::Unknown => true,
        HistoryEntry::Checkpoint(checkpoint) => {
            !local && !matches!(checkpoint.kind.as_str(), "compaction" | "model_change")
        }
        // A fired loop paints on its prompt, so no server notice paints a row.
        HistoryEntry::Notice(_) => !local,
        _ => false,
    }
}

/// The kind a group counts an effect under: unknown or missing kinds share `tool`.
pub fn label_kind(kind: Option<&str>) -> &str {
    match kind {
        Some(kind) if category(kind).is_some() => kind,
        _ => "tool",
    }
}

/// The verb and noun of `{verb} {count} {noun}`, e.g. `read 10 files`.
fn category_terms(kind: &str, count: usize, running: bool) -> (&'static str, &'static str) {
    let (done, active, one, many) =
        category(kind).unwrap_or(("called", "calling", "tool", "tools"));
    let verb = if running { active } else { done };
    let noun = if count == 1 { one } else { many };
    (verb, noun)
}

/// `a`, `a and b`, `a, b and c`.
fn join_and(items: &[String]) -> String {
    match items.split_last() {
        Some((last, rest)) if !rest.is_empty() => format!("{} and {last}", rest.join(", ")),
        _ => items.join(""),
    }
}

/// Past verb, running verb, and singular and plural nouns of a known kind.
fn category(kind: &str) -> Option<(&'static str, &'static str, &'static str, &'static str)> {
    Some(match kind {
        "file_read" => ("read", "reading", "file", "files"),
        "file_edit" => ("edited", "editing", "file", "files"),
        "file_write" => ("wrote", "writing", "file", "files"),
        "file_search" => ("ran", "running", "search", "searches"),
        "shell" => ("ran", "running", "command", "commands"),
        "web_search" => ("ran", "running", "web search", "web searches"),
        "web_fetch" => ("fetched", "fetching", "page", "pages"),
        "todo" => ("updated todos", "updating todos", "time", "times"),
        "user_question" => ("asked", "asking", "question", "questions"),
        "skill" => ("loaded", "loading", "skill", "skills"),
        "subagent" => ("ran", "running", "subagent", "subagents"),
        "worktree" => ("created", "creating", "worktree", "worktrees"),
        _ => return None,
    })
}

/// Hook lifecycle notices are not rendered inline, except a completed hook with
/// content and a name whose `(scope, toolCallId)` container is still open.
pub(super) fn is_hidden_hook_notice(entry: &HistoryEntry, prior: &[StoredEntry]) -> bool {
    let HistoryEntry::Notice(notice) = entry else {
        return false;
    };
    match notice.detail.as_ref().and_then(|d| d.kind.as_deref()) {
        Some("hook_run_started" | "hook_run_completed" | "hook_started") => true,
        Some("hook_completed") => notice
            .detail
            .as_ref()
            .is_none_or(|d| !hook_line_mounts(d, prior)),
        _ => false,
    }
}

/// Python mounts the pre-tool hook container before its tool call's anchor
/// (`mount_callback(container, before=anchor)`), so the scoped notices render
/// above the paired tool row: the entry id they insert ahead of, when it exists.
pub(super) fn pre_tool_anchor(entry: &HistoryEntry) -> Option<String> {
    let HistoryEntry::Notice(notice) = entry else {
        return None;
    };
    let detail = notice.detail.as_ref()?;
    if detail.scope.as_deref() != Some("pre_tool") {
        return None;
    }
    match detail.kind.as_deref() {
        Some(kind) if kind.starts_with("hook_") => detail.tool_call_id.clone(),
        _ => None,
    }
}

/// Python `_handle_hook_completed` mounts `HookSystemMessageLine` only when the
/// content and hook name are set and the `(scope, toolCallId)` container a
/// `hook_run_started` opened is still registered.
fn hook_line_mounts(detail: &NoticeDetail, prior: &[StoredEntry]) -> bool {
    let content = detail.content.as_deref().is_some_and(|c| !c.is_empty());
    let name = detail.hook_name.is_some();
    content && name && container_open(detail, prior)
}

/// Python `_hook_container_key`: the post-agent run keys the whole turn, the
/// tool-scoped runs key on the tool call id.
fn container_key(detail: &NoticeDetail) -> String {
    match detail.scope.as_deref() {
        Some("post_agent") => "agent_turn".to_owned(),
        scope => format!(
            "{}:{}",
            scope.unwrap_or(""),
            detail.tool_call_id.as_deref().unwrap_or("")
        ),
    }
}

/// Whether a `hook_run_started` opened the key and no `hook_run_completed`
/// closed it yet (`_hook_containers` is popped by the run's completion).
fn container_open(detail: &NoticeDetail, prior: &[StoredEntry]) -> bool {
    let key = container_key(detail);
    let mut open = false;
    for stored in prior {
        let HistoryEntry::Notice(notice) = &stored.typed else {
            continue;
        };
        let Some(prior_detail) = notice.detail.as_ref() else {
            continue;
        };
        if container_key(prior_detail) != key {
            continue;
        }
        match prior_detail.kind.as_deref() {
            Some("hook_run_started") => open = true,
            Some("hook_run_completed") => open = false,
            _ => {}
        }
    }
    open
}
