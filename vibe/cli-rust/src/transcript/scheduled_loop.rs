//! A fired scheduled loop annotates the user prompt it started instead of rendering itself.

use serde_json::Value;

use super::{StoredEntry, Transcript};
use crate::server::HistoryEntry;

/// The loop that sent a user prompt, and when it fired (epoch milliseconds).
#[derive(Clone)]
pub struct FiredLoop {
    pub loop_id: String,
    pub fired_at: Option<u64>,
}

pub(super) fn is_fired(entry: &HistoryEntry) -> bool {
    matches!(
        entry,
        HistoryEntry::Notice(notice)
            if notice.detail.as_ref().and_then(|detail| detail.kind.as_deref())
                == Some("scheduled_loop_fired")
    )
}

pub(super) fn fired(entry: &HistoryEntry) -> Option<FiredLoop> {
    let HistoryEntry::Notice(notice) = entry else {
        return None;
    };
    is_fired(entry).then(|| FiredLoop {
        loop_id: notice
            .detail
            .as_ref()
            .and_then(|detail| detail.loop_id.clone())
            .unwrap_or_default(),
        fired_at: notice.created_at,
    })
}

/// The `vibe.scheduled_loop` display marker a fired prompt carries; it survives forks.
pub(super) fn from_display(raw: &Value) -> Option<FiredLoop> {
    let marker = raw
        .pointer("/userDisplayContent/content")?
        .as_array()?
        .iter()
        .find(|block| block.get("type").and_then(Value::as_str) == Some("vibe.scheduled_loop"))?;
    Some(FiredLoop {
        loop_id: marker.get("loopId")?.as_str()?.to_owned(),
        fired_at: marker.get("firedAt").and_then(Value::as_u64),
    })
}

/// A loop prompt's title (its first line) and the instructions below it.
pub fn split_prompt(prompt: &str) -> (&str, &str) {
    let prompt = prompt.trim();
    prompt
        .split_once('\n')
        .map_or((prompt, ""), |(title, body)| (title.trim(), body.trim()))
}

/// Position of the user prompt the fired loop started in the same turn.
pub(super) fn prompt_index(prior: &[StoredEntry], notice: &Value) -> Option<usize> {
    let turn = notice.get("turnId").and_then(Value::as_str)?;
    prior.iter().rposition(|stored| {
        !stored.local
            && stored.raw.get("turnId").and_then(Value::as_str) == Some(turn)
            && matches!(&stored.typed, HistoryEntry::Message(message) if message.role == "user")
    })
}

impl Transcript {
    /// The loop prompt `id` names, or the one the fired notice `id` annotated.
    pub fn loop_prompt_of(&self, id: &str) -> Option<&str> {
        let &index = self.indices.get(id)?;
        let stored = &self.entries[index];
        let prompt = match is_fired(&stored.typed) {
            true => prompt_index(&self.entries[..index], &stored.raw)?,
            false => index,
        };
        let prompt = &self.entries[prompt];
        prompt.fired_loop.is_some().then_some(prompt.id.as_str())
    }
}
