//! Which entries render, and the summary of each packed tool group.

use super::StoredEntry;
use crate::server::{HistoryEntry, NoticeDetail};

pub(super) const KEY_PREFIX: &str = "tool-group:";

#[derive(Clone, Copy)]
pub enum Outcome {
    Success,
    Error,
    Muted,
}

pub struct Group<'a> {
    pub key: String,
    pub first: bool,
    pub last: bool,
    pub finalized: bool,
    pub kinds: Vec<&'a str>,
    pub reasoning: bool,
    pub outcome: Outcome,
}

impl Group<'_> {
    pub fn label(&self, running: bool) -> String {
        let mut labels = self
            .kinds
            .iter()
            .map(|kind| category_label(kind, running))
            .collect::<Vec<_>>();
        if self.reasoning {
            labels.push(if running { "thinking" } else { "thought" });
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
    effect.detail.as_ref()?.kind.as_deref()
}

pub fn outcome(entry: &HistoryEntry) -> Option<Outcome> {
    let HistoryEntry::Effect(effect) = entry else {
        return None;
    };
    let state = effect.state.as_ref()?;
    match state.status.as_deref() {
        Some("failed") => Some(Outcome::Error),
        Some("cancelled" | "skipped") => Some(Outcome::Muted),
        Some("completed") if !effect.success() => Some(Outcome::Error),
        Some("completed") => Some(Outcome::Success),
        _ => None,
    }
}

fn category_label(kind: &str, running: bool) -> &'static str {
    match (kind, running) {
        ("file_read", true) => "reading files",
        ("file_read", false) => "read files",
        ("file_edit", true) => "editing files",
        ("file_edit", false) => "edited files",
        ("file_write", true) => "writing files",
        ("file_write", false) => "wrote files",
        ("file_search", true) => "searching files",
        ("file_search", false) => "searched files",
        ("shell", true) => "running commands",
        ("shell", false) => "ran commands",
        ("web_search", true) => "searching the web",
        ("web_search", false) => "searched the web",
        ("web_fetch", true) => "fetching pages",
        ("web_fetch", false) => "fetched pages",
        ("todo", true) => "updating todos",
        ("todo", false) => "updated todos",
        ("user_question", true) => "asking questions",
        ("user_question", false) => "asked questions",
        ("skill", true) => "loading skills",
        ("skill", false) => "loaded skills",
        ("subagent", true) => "running subagents",
        ("subagent", false) => "ran subagents",
        ("worktree", true) => "creating worktrees",
        ("worktree", false) => "created worktrees",
        (_, true) => "calling tools",
        (_, false) => "called tools",
    }
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
