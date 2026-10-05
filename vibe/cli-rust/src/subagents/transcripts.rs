//! Bounded LRU of child transcripts (Python `SubagentTranscripts`).

use std::collections::{HashMap, HashSet};

use serde_json::{json, Value};

use crate::server::{PublicChildSession, SessionStatus};
use crate::subagents::child::{
    ChildTranscript, FetchState, Severity, MAX_CACHED_SUBAGENT_TRANSCRIPTS, READY_MESSAGE,
};
use crate::subagents::is_active;
use crate::transcript::local;

/// Bounded LRU of child transcripts (Python `SubagentTranscripts`).
#[derive(Default)]
pub struct SubagentTranscripts {
    ordered: Vec<(String, ChildTranscript)>,
    selected: Option<String>,
    pub parent_instructions: HashMap<String, Value>,
    pub active_session_ids: HashSet<String>,
    pub scroll_offsets: HashMap<Option<String>, u16>,
}

impl SubagentTranscripts {
    pub fn child(&self, session_id: &str) -> Option<&ChildTranscript> {
        self.ordered
            .iter()
            .find(|(id, _)| id == session_id)
            .map(|(_, transcript)| transcript)
    }

    /// Entries currently cached, for tests.
    pub fn len(&self) -> usize {
        self.ordered.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ordered.is_empty()
    }

    pub fn child_mut(&mut self, session_id: &str) -> Option<&mut ChildTranscript> {
        self.ordered
            .iter_mut()
            .find(|(id, _)| id == session_id)
            .map(|(_, transcript)| transcript)
    }

    /// Python `SubagentTranscripts.select`: drop the previous selection's
    /// completeness, keep the picked one most recent; false when not cached.
    pub fn select(&mut self, session_id: Option<&str>) -> bool {
        if self.selected.as_deref() != session_id {
            if let Some(previous) = self.selected.clone() {
                if let Some(entry) = self.child_mut(&previous) {
                    entry.history_complete = false;
                }
            }
        }
        self.selected = session_id.map(str::to_owned);
        let Some(session_id) = session_id else {
            return true;
        };
        let found = self.position(session_id);
        if let Some(index) = found {
            let entry = self.ordered.remove(index);
            self.ordered.push(entry);
        }
        found.is_some()
    }

    /// Python `prepare`: mount the loading placeholder, evicting the oldest
    /// entry beyond the cap while never evicting the viewed child.
    pub fn prepare(&mut self, session_id: &str) {
        if let Some(index) = self.position(session_id) {
            let entry = self.ordered.remove(index);
            self.ordered.push(entry);
            return;
        }
        self.ordered
            .push((session_id.to_owned(), ChildTranscript::default()));
        while self.ordered.len() > MAX_CACHED_SUBAGENT_TRANSCRIPTS {
            let Some(index) = self
                .ordered
                .iter()
                .position(|(id, _)| Some(id) != self.selected.as_ref())
            else {
                break;
            };
            self.ordered.remove(index);
        }
    }

    /// Python `replace_history`: tail-merge against the cached history,
    /// prepend the remembered parent instruction, rebuild the transcript.
    pub fn replace_history(
        &mut self,
        session_id: &str,
        latest: Vec<Value>,
        history_complete: bool,
        show_thinking: bool,
    ) -> bool {
        self.prepare(session_id);
        self.remember_parent_instruction_from_history(session_id, &latest);
        let (cached_history, cached_complete) = {
            let transcript = self.child_mut(session_id).expect("prepare cached the id");
            (transcript.history.clone(), transcript.history_complete)
        };
        let rendered = if history_complete || cached_history.is_none() || !cached_complete {
            latest.clone()
        } else {
            merge_history_tail(cached_history.as_deref().unwrap_or(&[]), &latest)
        };
        // The child transcript starts with the parent's spawn task (Python
        // synthesizes `parent-instruction:<child>` and prepends it).
        let rendered = match self.parent_instructions.get(session_id) {
            Some(instruction)
                if !rendered
                    .iter()
                    .any(|entry| entry_id(entry) == entry_id(instruction)) =>
            {
                let mut prepended = vec![instruction.clone()];
                prepended.extend(rendered);
                prepended
            }
            _ => rendered,
        };
        self.rebuild(session_id, rendered, history_complete, show_thinking)
    }

    fn rebuild(
        &mut self,
        session_id: &str,
        rendered: Vec<Value>,
        history_complete: bool,
        show_thinking: bool,
    ) -> bool {
        let Some(transcript) = self.child_mut(session_id) else {
            return false;
        };
        let previous = transcript.history.clone();
        transcript.history_complete = history_complete || transcript.history_complete;
        if previous.is_some() && previous.as_deref() == Some(rendered.as_slice()) {
            transcript.fetch_state = None;
            return false;
        }
        transcript.fetch_state = None;
        // Rebuild in place: a fresh `Transcript::default()` restarts the
        // revision stream while the child's render cache survives, so a
        // same-length rebuild would reuse stale heights and layouts.
        transcript.transcript.clear();
        for entry in &rendered {
            transcript.transcript.add(&json!({"entry": entry}));
        }
        transcript.transcript.set_show_reasoning(show_thinking);
        for (index, (message, severity)) in transcript.local_user_messages.iter().enumerate() {
            let id = local_message_id(session_id, index);
            local::add_message(
                &mut transcript.transcript,
                &id,
                severity_role(*severity),
                message,
            );
        }
        transcript.history = Some(rendered);
        true
    }

    /// Python `announce_ready_transition`: once per active period, append the
    /// ready message when a child leaves running/blocked for idle. A new
    /// active period retires the parked row first, so at most one shows and
    /// only while the child is actually idle (Python stacks one per period).
    pub fn announce_ready_transition(&mut self, session: &PublicChildSession) -> bool {
        if is_active(session.status) {
            if self.active_session_ids.insert(session.id.clone()) {
                self.remove_ready_message(&session.id);
            }
            return false;
        }
        if session.status != SessionStatus::Idle {
            self.active_session_ids.remove(&session.id);
            return false;
        }
        if !self.active_session_ids.remove(&session.id) {
            return false;
        }
        self.append_local_user_message(&session.id, READY_MESSAGE, Severity::Info);
        true
    }

    /// Drop the parked ready row: its period ended when the child started work
    /// on a new instruction (Python keeps every row; one shows at a time here).
    /// Local ids are positional, so every later row re-renders under a fresh
    /// one; reusing the vacated id would overwrite a live row on the next append.
    fn remove_ready_message(&mut self, session_id: &str) {
        let Some(transcript) = self.child_mut(session_id) else {
            return;
        };
        let Some(index) = transcript
            .local_user_messages
            .iter()
            .position(|(message, _)| message == READY_MESSAGE)
        else {
            return;
        };
        for i in index..transcript.local_user_messages.len() {
            transcript
                .transcript
                .remove(&local_message_id(session_id, i));
        }
        transcript.local_user_messages.remove(index);
        for (i, (message, severity)) in transcript
            .local_user_messages
            .iter()
            .enumerate()
            .skip(index)
        {
            let id = local_message_id(session_id, i);
            local::add_message(
                &mut transcript.transcript,
                &id,
                severity_role(*severity),
                message,
            );
        }
    }

    /// Python `append_local_user_message`: a client-owned row the server never sees.
    pub fn append_local_user_message(
        &mut self,
        session_id: &str,
        content: &str,
        severity: Severity,
    ) {
        self.prepare(session_id);
        let Some(transcript) = self.child_mut(session_id) else {
            return;
        };
        let index = transcript.local_user_messages.len();
        transcript
            .local_user_messages
            .push((content.to_owned(), severity));
        let id = local_message_id(session_id, index);
        local::add_message(
            &mut transcript.transcript,
            &id,
            severity_role(severity),
            content,
        );
    }

    /// Python `show_error`: replace the loading placeholder unless history exists.
    pub fn show_error(&mut self, session_id: &str) {
        self.prepare(session_id);
        let Some(transcript) = self.child_mut(session_id) else {
            return;
        };
        if transcript.history.is_none() {
            transcript.fetch_state = Some(FetchState::Error);
        }
    }

    /// Python `remember_parent_instruction` from live events: first one wins.
    pub fn remember_instruction(&mut self, instruction: Value) {
        let Some(session_id) = instruction.get("sessionId").and_then(Value::as_str) else {
            return;
        };
        self.parent_instructions
            .entry(session_id.to_owned())
            .or_insert(instruction);
    }

    /// Python `_remember_parent_instruction` from fetched history: latest wins.
    fn remember_parent_instruction_from_history(&mut self, session_id: &str, history: &[Value]) {
        let Some(instruction) = history.iter().find(|entry| {
            entry.get("type").and_then(Value::as_str) == Some("message")
                && entry.get("role").and_then(Value::as_str) == Some("user")
                && entry.get("source").and_then(Value::as_str) == Some("turn_start")
        }) else {
            return;
        };
        self.parent_instructions
            .insert(session_id.to_owned(), instruction.clone());
    }

    /// Python `clear` on `_reset_subagent_views`.
    pub fn clear(&mut self) {
        self.ordered.clear();
        self.selected = None;
        self.parent_instructions.clear();
        self.active_session_ids.clear();
        self.scroll_offsets.clear();
    }

    fn position(&self, session_id: &str) -> Option<usize> {
        self.ordered.iter().position(|(id, _)| id == session_id)
    }
}

/// Python `_merge_history_tail`: keep the cached prefix up to the first shared
/// id, then append the new tail.
pub fn merge_history_tail(history: &[Value], latest: &[Value]) -> Vec<Value> {
    if latest.is_empty() {
        return Vec::new();
    }
    let indices: HashMap<&str, usize> = history
        .iter()
        .enumerate()
        .filter_map(|(index, entry)| entry_id(entry).map(|id| (id, index)))
        .collect();
    let overlap = latest
        .iter()
        .filter_map(|entry| entry_id(entry).and_then(|id| indices.get(id)).copied())
        .next()
        .unwrap_or(history.len());
    let mut merged = history[..overlap].to_vec();
    merged.extend(latest.iter().cloned());
    merged
}

pub(super) fn entry_id(entry: &Value) -> Option<&str> {
    entry.get("id").and_then(Value::as_str)
}

pub(super) fn local_message_id(session_id: &str, index: usize) -> String {
    format!("subagent-local-{session_id}-{index}")
}

/// Python renders these with `UserMessage(severity=...)`: the role carries the
/// severity so the transcript renderer styles the border and the text.
fn severity_role(severity: Severity) -> &'static str {
    match severity {
        Severity::Info => "subagent_info",
        Severity::Error => "subagent_error",
    }
}
