//! Goal notices paint a row, anchored on the turn they belong to.

use serde_json::Value;

use super::StoredEntry;
use crate::server::HistoryEntry;

pub fn is_goal(entry: &HistoryEntry) -> bool {
    matches!(
        entry,
        HistoryEntry::Notice(notice)
            if notice.detail.as_ref().and_then(|detail| detail.kind.as_deref()) == Some("goal")
    )
}

/// The events recorded once the turn that stopped the goal ended. A `paused`
/// notice carries a turn only when the model paused the goal in it.
const STOP_EVENTS: [&str; 6] = [
    "completed",
    "blocked",
    "budget_limited",
    "usage_limited",
    "interrupted",
    "paused",
];

/// Where a goal notice tied to a turn goes, when not at the end: a `continued`
/// notice heads the output its turn streamed before it was recorded, and a stop
/// notice follows its turn when a later turn already started.
pub(super) fn anchor(prior: &[StoredEntry], entry: &HistoryEntry, raw: &Value) -> Option<usize> {
    let HistoryEntry::Notice(notice) = entry else {
        return None;
    };
    let detail = notice.detail.as_ref()?;
    if detail.kind.as_deref() != Some("goal") {
        return None;
    }
    let turn = raw.get("turnId").and_then(Value::as_str)?;
    let in_turn = |stored: &StoredEntry| !stored.local && turn_of(stored) == Some(turn);
    match detail.event.as_deref()? {
        "continued" => prior.iter().position(|stored| {
            in_turn(stored)
                && !matches!(&stored.typed, HistoryEntry::Message(message) if message.role == "user")
        }),
        event if STOP_EVENTS.contains(&event) => {
            let last = prior.iter().rposition(in_turn)?;
            let later = prior[last + 1..]
                .iter()
                .any(|stored| !stored.local && turn_of(stored).is_some_and(|id| id != turn));
            later.then_some(last + 1)
        }
        _ => None,
    }
}

fn turn_of(stored: &StoredEntry) -> Option<&str> {
    stored.raw.get("turnId").and_then(Value::as_str)
}
