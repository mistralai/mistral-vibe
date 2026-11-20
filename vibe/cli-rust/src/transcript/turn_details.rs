//! A turn's details: the steps between its prompt and its final answer, collapsed into one group.

use super::{StoredEntry, Transcript};
use crate::server::{is_discarded_draft, HistoryEntry};

/// Key prefix of a turn's `Worked for` group in the expanded set, followed by its prompt's entry id.
pub const KEY_PREFIX: &str = "turn-details:";

/// The steps of a settled turn, shown under one `Worked for` group above its final answer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TurnDetails {
    /// The group's key in the expanded set: [`KEY_PREFIX`] and the prompt's entry id.
    pub key: String,
    /// Entry index of the first step, which carries the group header.
    pub first: usize,
    /// Entry index of the last step.
    pub last: usize,
    /// Entry index of the last step that paints a row, where the rail closes; set by the layout.
    pub close: usize,
    /// Sorted rows of the user's own (a steer, a `!` command) between the first and last step.
    pub outside: Vec<usize>,
    /// Intermediate assistant messages in the group.
    pub messages: usize,
    /// Tool calls in the group.
    pub tools: usize,
    /// Milliseconds from the prompt to the final answer, when both carry a creation time.
    pub elapsed_ms: Option<u64>,
}

impl TurnDetails {
    /// Whether the entry at `index` is one of the group's steps.
    pub fn contains(&self, index: usize) -> bool {
        (self.first..=self.last).contains(&index) && self.outside.binary_search(&index).is_err()
    }

    /// `Worked for 1m 12s · 2 messages · 3 tool calls`, leaving out an unknown duration and zero counts.
    pub fn label(&self) -> String {
        let counts = [(self.messages, "message"), (self.tools, "tool call")];
        let mut parts: Vec<String> = self.elapsed_ms.map(worked_for).into_iter().collect();
        parts.extend(
            counts
                .into_iter()
                .filter(|(count, _)| *count > 0)
                .map(|(count, noun)| match count {
                    1 => format!("1 {noun}"),
                    _ => format!("{count} {noun}s"),
                }),
        );
        let label = parts.join(" · ");
        match (self.elapsed_ms, label.is_empty()) {
            (Some(_), _) => label,
            (None, true) => "Worked".to_owned(),
            (None, false) => format!("Worked · {label}"),
        }
    }
}

/// `Worked for <1s`, `Worked for 12s`, `Worked for 1m 12s`, `Worked for 1h 2m 3s`.
fn worked_for(elapsed_ms: u64) -> String {
    let seconds = elapsed_ms / 1_000;
    let (hours, minutes, seconds) = (seconds / 3_600, seconds % 3_600 / 60, seconds % 60);
    let elapsed = match (hours, minutes, seconds) {
        (0, 0, 0) => "<1s".to_owned(),
        (0, 0, _) => format!("{seconds}s"),
        (0, _, _) => format!("{minutes}m {seconds}s"),
        _ => format!("{hours}h {minutes}m {seconds}s"),
    };
    format!("Worked for {elapsed}")
}

impl Transcript {
    /// The details of the settled turn the prompt at `index` opens; `None` while it runs or was interrupted.
    pub fn turn_details(&self, index: usize, running: bool) -> Option<TurnDetails> {
        let stored = self.entries.get(index)?;
        if !self.is_prompt(index) || stored.pending || stored.fired_loop.is_some() {
            return None;
        }
        let next = self.next_prompt(index);
        if running && next.is_none() {
            return None;
        }
        let end = next.unwrap_or(self.entries.len());
        let answer = (index + 1..end).rev().find(|&at| self.is_answer(at))?;
        // An interrupted turn has no final answer: its last message may be a step cut short.
        let after = |at: usize| at > answer && self.is_model_step(at) || self.interrupted(at);
        if (answer..end).any(after) {
            return None;
        }
        let (mut first, mut last, mut outside) = (None, None, Vec::new());
        let (mut messages, mut tools, mut thought) = (0, 0, false);
        for at in index + 1..answer {
            if !self.in_details(at) {
                outside.extend(self.is_users(at).then_some(at));
                continue;
            }
            first.get_or_insert(at);
            last = Some(at);
            match &self.entries[at].typed {
                HistoryEntry::Message(_) if self.is_shown_reply(at) => messages += 1,
                HistoryEntry::Effect(_) => tools += 1,
                HistoryEntry::Reasoning(_) => thought = true,
                _ => {}
            }
        }
        let (first, last) = (first?, last?);
        outside.retain(|&at| at > first && at < last);
        (messages + tools > 0 || thought).then(|| TurnDetails {
            key: format!("{KEY_PREFIX}{}", stored.id),
            first,
            last,
            close: last,
            outside,
            messages,
            tools,
            elapsed_ms: elapsed_ms(stored, &self.entries[answer]),
        })
    }

    /// Every prompt's `Worked for` group key, which Ctrl+O unfolds with the tool groups.
    pub fn details_keys(&self) -> Vec<String> {
        let prompts = (0..self.entries.len()).filter(|&at| self.is_prompt(at));
        prompts
            .map(|at| format!("{KEY_PREFIX}{}", self.entries[at].id))
            .collect()
    }

    /// The `Worked for` group key of the prompt `id`, set ahead of its details while Ctrl+O expands all.
    pub fn details_key(&self, id: &str) -> Option<String> {
        let index = *self.indices.get(id)?;
        self.is_prompt(index).then(|| format!("{KEY_PREFIX}{id}"))
    }

    /// The last step of `details` that paints a row: a folded tool group paints only its first call.
    pub fn closing_step(&self, details: &TurnDetails, expanded: impl Fn(&str) -> bool) -> usize {
        self.lines_from(details.first)
            .take_while(|entry| entry.index <= details.last)
            .filter(|entry| details.contains(entry.index))
            .filter(|entry| {
                (entry.group.as_ref()).is_none_or(|group| group.first || expanded(&group.key))
            })
            .last()
            .map_or(details.last, |entry| entry.index)
    }

    /// The settled prompt whose turn the entry at `index` belongs to, if any.
    pub fn turn_prompt(&self, index: usize) -> Option<usize> {
        (0..index.min(self.entries.len()))
            .rev()
            .find(|&at| self.is_prompt(at) && !self.entries[at].pending)
    }

    /// An interrupt marker, or an entry the server discarded because the turn was interrupted.
    fn interrupted(&self, index: usize) -> bool {
        let stored = &self.entries[index];
        matches!(stored.typed, HistoryEntry::Interrupt(_))
            || stored.raw["outcome"]["cause"] == "interrupt"
    }

    /// Whether the entry at `index` folds into its turn's group: a painted row not of the user's own.
    fn in_details(&self, index: usize) -> bool {
        let stored = &self.entries[index];
        !self.is_users(index)
            && !self.is_hidden(index)
            && !super::grouping::renders_nothing(&stored.typed, stored.local)
    }

    /// The settled prompt opening the turn after the one `index` opens; a queued one has no turn yet.
    fn next_prompt(&self, index: usize) -> Option<usize> {
        (index + 1..self.entries.len())
            .find(|&at| self.is_prompt(at))
            .filter(|&at| !self.entries[at].pending)
    }

    /// A shown user message opening a turn: not a subagent notification, a packed prompt or a steer.
    fn is_prompt(&self, index: usize) -> bool {
        let stored = &self.entries[index];
        is_user(stored)
            && !self.is_hidden(index)
            && !stored.follows_user
            && stored.raw["source"] != "turn_steer"
    }

    /// A model step: a server tool call, reasoning or kept reply; not a notice, checkpoint or client row.
    fn is_model_step(&self, index: usize) -> bool {
        let stored = &self.entries[index];
        let step = matches!(
            stored.typed,
            HistoryEntry::Effect(_) | HistoryEntry::Reasoning(_)
        );
        let shown = !stored.local && !self.is_hidden(index);
        shown && !self.is_users(index) && (step || self.is_shown_reply(index))
    }

    /// The user's own row: a message, or a `!` command, which stays shown like a steer.
    fn is_users(&self, index: usize) -> bool {
        let stored = &self.entries[index];
        let manual = !super::grouping::groups_with_tools(&stored.typed)
            && super::grouping::effect_kind(&stored.typed) == Some("shell");
        is_user(stored) || manual
    }

    fn is_answer(&self, index: usize) -> bool {
        !self.entries[index].local && !self.is_hidden(index) && self.is_shown_reply(index)
    }

    /// A non-empty assistant message the model kept.
    fn is_shown_reply(&self, index: usize) -> bool {
        let stored = &self.entries[index];
        match &stored.typed {
            HistoryEntry::Message(message) => {
                message.role == "assistant"
                    && !is_discarded_draft(&stored.raw)
                    && !message.text("").trim().is_empty()
            }
            _ => false,
        }
    }
}

fn elapsed_ms(prompt: &StoredEntry, answer: &StoredEntry) -> Option<u64> {
    let created = |stored: &StoredEntry| stored.raw["createdAt"].as_u64();
    created(answer)?.checked_sub(created(prompt)?)
}

fn is_user(stored: &StoredEntry) -> bool {
    matches!(&stored.typed, HistoryEntry::Message(message) if message.role == "user")
}
