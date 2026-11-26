//! Subagent effect and completion-notification projection.

use crate::server::{HistoryEntry, MessageContent};
use serde_json::Value;

pub(super) fn is_effect(entry: &HistoryEntry) -> bool {
    matches!(entry, HistoryEntry::Effect(effect) if effect.is_subagent())
}

pub(super) struct Notification {
    pub output: String,
    agent_name: Option<String>,
    child_session_id: Option<String>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum EffectMatch {
    ChildSession,
    AgentName,
}

/// Harness 0.4.x runtime-message format; unknown shapes remain user messages (ADR 0014).
pub(super) fn notification(entry: &HistoryEntry) -> Option<Notification> {
    let HistoryEntry::Message(message) = entry else {
        return None;
    };
    if message.role != "user" || message.source.as_deref() != Some("harness") {
        return None;
    }
    let texts = message
        .content
        .iter()
        .filter_map(|block| match block {
            MessageContent::Text { text } => Some(text.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>();
    let raw = texts
        .first()?
        .strip_prefix("Runtime notification:")?
        .trim_start();
    let (payload, inline_output) = raw.split_once("\n\n").unwrap_or((raw, ""));
    let value: Value = serde_json::from_str(payload).ok()?;
    if value.pointer("/source/type").and_then(Value::as_str) != Some("subagent") {
        return None;
    }
    let mut output = Vec::new();
    if !inline_output.trim().is_empty() {
        output.push(inline_output);
    }
    output.extend(
        texts
            .into_iter()
            .skip(1)
            .filter(|text| !text.trim().is_empty()),
    );
    if output.is_empty() {
        if let Some(message) = value.get("message").and_then(Value::as_str) {
            output.push(message);
        }
    }
    let child_session_id = value
        .get("id")
        .and_then(Value::as_str)
        .and_then(|id| id.strip_prefix("subagent:"))
        .and_then(|id| id.split(':').next())
        .map(str::to_owned);
    let agent_name = value
        .pointer("/source/agent_name")
        .and_then(Value::as_str)
        .map(str::to_owned);
    Some(Notification {
        output: output.join("\n\n"),
        agent_name,
        child_session_id,
    })
}

pub(super) fn effect_match(
    raw: &Value,
    entry: &HistoryEntry,
    notification: &Notification,
) -> Option<EffectMatch> {
    if !is_effect(entry) {
        return None;
    }
    let effect_child = raw
        .pointer("/detail/childSessionId")
        .and_then(Value::as_str);
    let notified_child = notification.child_session_id.as_deref();
    if let (Some(effect), Some(notified)) = (effect_child, notified_child) {
        return (effect == notified).then_some(EffectMatch::ChildSession);
    }
    effect_agent_name(raw)
        .zip(notification.agent_name.as_deref())
        .filter(|(effect, notified)| effect == notified)
        .map(|_| EffectMatch::AgentName)
}

fn effect_agent_name(raw: &Value) -> Option<&str> {
    raw.pointer("/detail/input/agentName")
        .and_then(Value::as_str)
        .or_else(|| raw.pointer("/detail/input/agent").and_then(Value::as_str))
}
