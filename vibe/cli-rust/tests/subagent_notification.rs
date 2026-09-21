//! Subagent completion notifications attach to the matching effect.

use serde_json::{json, Value};
use vibe_rs::transcript::Transcript;

fn add(transcript: &mut Transcript, entry: Value) {
    transcript.add(&json!({"entry": entry}));
}

#[test]
fn child_session_match_outranks_later_agent_name_match() {
    let mut transcript = Transcript::default();
    add(
        &mut transcript,
        json!({
            "id": "spawn",
            "type": "effect",
            "detail": {
                "kind": "subagent",
                "toolName": "subagent.spawn",
                "input": {"agentName": "explore-1"},
                "childSessionId": "child-session",
                "display": {"verb": "Exploring"}
            },
            "state": {"status": "completed"}
        }),
    );
    add(
        &mut transcript,
        json!({
            "id": "wait",
            "type": "effect",
            "detail": {
                "kind": "tool",
                "toolName": "subagent.wait",
                "input": {"agentName": "explore-1"},
                "display": {"verb": "Waiting"}
            },
            "state": {"status": "running"}
        }),
    );
    // Harness 0.4.5 `_notifications.notification_id` embeds the child session ID.
    add(
        &mut transcript,
        json!({
            "id": "notification",
            "type": "message",
            "role": "user",
            "source": "harness",
            "content": [
                {"type": "text", "text": concat!(
                    "Runtime notification:\n",
                    r#"{"id":"subagent:child-session:explore-1:1:completed","source":{"type":"subagent","agent_name":"explore-1"}}"#
                )},
                {"type": "text", "text": "spawn result"}
            ]
        }),
    );

    assert_eq!(
        transcript.entry(0).unwrap().attached_output,
        Some("spawn result")
    );
    assert_eq!(transcript.entry(1).unwrap().attached_output, None);
}

#[test]
fn agent_name_remains_the_fallback_without_a_linked_spawn() {
    let mut transcript = Transcript::default();
    add(
        &mut transcript,
        json!({
            "id": "wait",
            "type": "effect",
            "detail": {
                "kind": "tool",
                "toolName": "subagent.wait",
                "input": {"agentName": "explore-1"},
                "display": {"verb": "Waiting"}
            },
            "state": {"status": "running"}
        }),
    );
    add(
        &mut transcript,
        json!({
            "id": "notification",
            "type": "message",
            "role": "user",
            "source": "harness",
            "content": [
                {"type": "text", "text": concat!(
                    "Runtime notification:\n",
                    r#"{"id":"subagent:child-session:explore-1:1:completed","source":{"type":"subagent","agent_name":"explore-1"}}"#
                )},
                {"type": "text", "text": "wait result"}
            ]
        }),
    );

    assert_eq!(
        transcript.entry(0).unwrap().attached_output,
        Some("wait result")
    );
}
