//! Which entries render, and the summary of each packed tool group.

use std::collections::HashSet;

use serde_json::Value;

use super::StoredEntry;
use crate::server::{CheckpointEntry, EffectEntry, HistoryEntry, NoticeDetail};

pub(crate) const KEY_PREFIX: &str = "tool-group:";

/// Calls per kind in first-seen order.
pub type KindCounts<'a> = Vec<(&'a str, usize)>;

pub struct Group<'a> {
    pub key: String,
    pub first: bool,
    pub last: bool,
    pub finalized: bool,
    /// Settled calls per kind; empty except on the group's first entry.
    pub kinds: KindCounts<'a>,
    /// Calls still running per kind; empty except on the group's first entry.
    pub running: KindCounts<'a>,
    pub reasoning: bool,
    /// A reasoning row of the live group is still streaming.
    pub thinking: bool,
}

impl Group<'_> {
    /// Running calls, then settled ones, then reasoning: `Calling 1 tool, called 2 tools, thought`.
    pub fn label(&self) -> String {
        let mut labels = segments(&self.running, true);
        labels.extend(segments(&self.kinds, false));
        if self.reasoning {
            labels.push(if self.thinking { "thinking" } else { "thought" }.to_owned());
        }
        let mut label = labels.join(", ");
        if let Some(first) = label.get_mut(0..1) {
            first.make_ascii_uppercase();
        }
        label
    }
}

/// Kinds sharing a verb merge into one segment: `ran 2 commands and 1 search`.
/// A wait reads as what the agent does, not as a counted call. The main view
/// hides a running one behind the waiting status line; a subagent's view
/// shows it, so it reads as still waiting there.
fn segments(kinds: &[(&str, usize)], running: bool) -> Vec<String> {
    let mut verbs: Vec<(String, Vec<String>)> = Vec::new();
    for &(kind, count) in kinds {
        if let Some(phrase) = uncounted(kind, running) {
            verbs.push((phrase.to_owned(), Vec::new()));
            continue;
        }
        let (verb, noun) = category_terms(kind, count, running);
        let counted = format!("{count} {noun}");
        match verbs.iter_mut().find(|(seen, _)| seen == verb) {
            Some((_, nouns)) => nouns.push(counted),
            None => verbs.push((verb.to_owned(), vec![counted])),
        }
    }
    verbs
        .into_iter()
        .map(|(verb, nouns)| match nouns.is_empty() {
            true => verb,
            false => format!("{verb} {}", join_and(&nouns)),
        })
        .collect()
}

/// The whole segment of a kind that reads without a count.
fn uncounted(kind: &str, running: bool) -> Option<&'static str> {
    (kind == "wait").then_some(match running {
        true => "waiting for background work",
        false => "waited for background work",
    })
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
/// server checkpoint other than a painted compaction or model change, or an
/// unrecognized type. Such rows must not break a run of tool calls into
/// consecutive blocks: all calls between them collapse into one block.
pub fn renders_nothing(entry: &HistoryEntry, local: bool) -> bool {
    if groups_with_tools(entry) {
        return false;
    }
    match entry {
        HistoryEntry::Callback(_) | HistoryEntry::Unknown => true,
        HistoryEntry::Checkpoint(checkpoint) => {
            !local && !(checkpoint.kind == "model_change" || paints_compaction(checkpoint))
        }
        // A fired loop paints on its prompt, so no server notice paints a row.
        HistoryEntry::Notice(_) => !local,
        _ => false,
    }
}

/// Whether a server checkpoint paints as a compaction row. `/compact` paints its
/// own local row, so a manual compaction paints none, live or resumed.
pub fn paints_compaction(checkpoint: &CheckpointEntry) -> bool {
    checkpoint.kind == "compaction"
        && checkpoint.details.get("trigger").and_then(Value::as_str) != Some("manual")
}

/// Whether a settled compaction checkpoint records a failed attempt.
pub fn compaction_failed(checkpoint: &CheckpointEntry) -> bool {
    checkpoint
        .details
        .get("error")
        .is_some_and(|error| !error.is_null())
}

/// The kind a group counts an effect under: unknown or missing kinds share `tool`.
pub fn label_kind(kind: Option<&str>) -> &str {
    match kind {
        Some(kind) if category(kind).is_some() => kind,
        _ => "tool",
    }
}

/// Kinds in first-seen order, as `(running, settled)`: a call runs while its
/// group is `live` and it has no verdict. File kinds count distinct paths, the
/// rest count calls.
pub(super) fn count_kinds<'a>(
    effects: impl Iterator<Item = &'a EffectEntry>,
    live: bool,
) -> (KindCounts<'a>, KindCounts<'a>) {
    let mut running = KindCounts::new();
    let mut settled = KindCounts::new();
    let mut paths = HashSet::new();
    for effect in effects {
        let kind = match effect.is_model_wait() {
            true => "wait",
            false => label_kind(effect.kind()),
        };
        let is_running = live && !effect.is_terminal();
        let kinds = match is_running {
            true => &mut running,
            false => &mut settled,
        };
        let repeat =
            file_path(kind, effect).is_some_and(|path| !paths.insert((is_running, kind, path)));
        match kinds.iter_mut().find(|(seen, _)| *seen == kind) {
            Some((_, count)) => *count += usize::from(!repeat),
            None => kinds.push((kind, 1)),
        }
    }
    (running, settled)
}

/// The target of a call whose kind counts files (`read 2 files`).
fn file_path<'a>(kind: &str, effect: &'a EffectEntry) -> Option<&'a str> {
    if !matches!(kind, "file_read" | "file_edit" | "file_write") {
        return None;
    }
    effect.input()?.get("filePath")?.as_str()
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

/// The pre-tool hook notices render above the paired tool row: the entry id
/// they insert ahead of, when it exists.
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

/// A completed hook mounts its system message line only when the
/// content and hook name are set and the `(scope, toolCallId)` container a
/// `hook_run_started` opened is still registered.
fn hook_line_mounts(detail: &NoticeDetail, prior: &[StoredEntry]) -> bool {
    let content = detail.content.as_deref().is_some_and(|c| !c.is_empty());
    let name = detail.hook_name.is_some();
    content && name && container_open(detail, prior)
}

/// The hook container key: a turn-level run keys its scope for the whole
/// turn, the tool-scoped runs key on the tool call id.
fn container_key(detail: &NoticeDetail) -> String {
    match detail.scope.as_deref() {
        Some("post_agent") => "agent_turn".to_owned(),
        Some("pre_agent") => "agent_turn_start".to_owned(),
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
