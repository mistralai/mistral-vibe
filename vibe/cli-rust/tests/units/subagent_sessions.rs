//! Child-session wire shapes, list batching, and display-name helpers.

use std::collections::HashSet;

use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::subagents::{active_batch, display_name, status_label, status_tone, StatusTone};

fn child(id: &str, status: SessionStatus, created_at: i64) -> PublicChildSession {
    PublicChildSession {
        id: id.into(),
        name: format!("{id}-name"),
        agent_type: "explore".into(),
        status,
        created_at,
        updated_at: created_at,
        ..Default::default()
    }
}

fn parse(json: serde_json::Value) -> PublicChildSession {
    serde_json::from_value(json).expect("wire shape parses")
}

#[test]
fn parses_the_child_session_wire_shape() {
    let session = parse(serde_json::json!({
        "id": "child-1",
        "name": "explore-1",
        "agentType": "explore",
        "status": {"type": "running", "activeTurnId": "turn-1"},
        "tokenUsage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
        "contextUsage": {"inputTokens": 40, "outputTokens": 60, "totalTokens": 100},
        "createdAt": 5,
        "updatedAt": 6,
    }));
    assert_eq!(session.status, SessionStatus::Running);
    assert_eq!(session.context_tokens(), 100);
}

#[test]
fn tokens_need_context_usage() {
    let mut session = child("c", SessionStatus::Idle, 1);
    session.token_usage.total_tokens = 42;
    assert_eq!(
        session.context_tokens(),
        0,
        "Python shows 0 without contextUsage"
    );
    session.context_usage = Some(Default::default());
    assert_eq!(session.context_tokens(), 0);
}

#[test]
fn unknown_status_tag_reads_as_unknown() {
    let session = parse(serde_json::json!({
        "id": "c",
        "name": "n",
        "agentType": "a",
        "status": {"type": "hibernating", "detail": "future server"},
        "createdAt": 1,
        "updatedAt": 1,
    }));
    assert_eq!(session.status, SessionStatus::Unknown);
    assert_eq!(status_label(session.status), "unknown");
    assert_eq!(status_tone(session.status), StatusTone::Muted);
}

#[test]
fn malformed_status_reads_as_unknown() {
    let session = parse(serde_json::json!({
        "id": "c",
        "name": "n",
        "agentType": "a",
        "status": "running",
        "createdAt": 1,
        "updatedAt": 1,
    }));
    assert_eq!(session.status, SessionStatus::Unknown);
}

#[test]
fn a_child_missing_required_fields_is_rejected() {
    let result = serde_json::from_value::<PublicChildSession>(serde_json::json!({
        "id": "c",
        "createdAt": 1,
        "updatedAt": 1,
    }));
    assert!(result.is_err());
}

#[test]
fn an_updated_without_a_child_session_is_rejected() {
    let result = serde_json::from_value::<vibe_rs::server::ChildSessionUpdatedParams>(
        serde_json::json!({"eventId": 7}),
    );
    assert!(result.is_err());
}

#[test]
fn status_labels_match_python() {
    let cases = [
        (SessionStatus::Running, "running", StatusTone::Success),
        (SessionStatus::Blocked, "blocked", StatusTone::Warning),
        (SessionStatus::Failed, "failed", StatusTone::Error),
        (SessionStatus::Archived, "stopped", StatusTone::Muted),
        (SessionStatus::Idle, "ready", StatusTone::Muted),
    ];
    for (status, label, tone) in cases {
        assert_eq!(status_label(status), label);
        assert_eq!(status_tone(status), tone);
    }
}

#[test]
fn replace_keeps_creation_order() {
    let mut subagents = vibe_rs::subagents::Subagents::default();
    subagents.replace_child_session(child("late", SessionStatus::Running, 9));
    subagents.replace_child_session(child("early", SessionStatus::Running, 2));
    subagents.replace_child_session(child("a-tie", SessionStatus::Running, 2));
    subagents.replace_child_session(child("late", SessionStatus::Idle, 9));
    let ids: Vec<&str> = subagents
        .sessions
        .iter()
        .map(|session| session.id.as_str())
        .collect();
    assert_eq!(ids, ["a-tie", "early", "late"]);
    assert_eq!(subagents.sessions[2].status, SessionStatus::Idle);
}

#[test]
fn batch_holds_children_until_the_batch_ends() {
    let mut known = HashSet::new();
    let mut batch = HashSet::new();
    // Batch with an active child: both listed.
    let rows = active_batch(
        &mut known,
        &mut batch,
        &[
            child("a", SessionStatus::Running, 1),
            child("b", SessionStatus::Idle, 2),
        ],
        None,
    );
    assert_eq!(ids(&rows), ["a", "b"]);
    // Both go idle in the same batch: still listed (the batch has not ended).
    let rows = active_batch(
        &mut known,
        &mut batch,
        &[
            child("a", SessionStatus::Idle, 1),
            child("b", SessionStatus::Idle, 2),
        ],
        None,
    );
    assert_eq!(ids(&rows), ["a", "b"]);
    // A later all-idle batch with no active child clears the hold.
    let rows = active_batch(
        &mut known,
        &mut batch,
        &[
            child("a", SessionStatus::Idle, 1),
            child("b", SessionStatus::Idle, 2),
        ],
        None,
    );
    assert_eq!(ids(&rows), ["a", "b"], "idle children always stay listed");
    // New ids only join while some child is active.
    let rows = active_batch(
        &mut known,
        &mut batch,
        &[
            child("a", SessionStatus::Idle, 1),
            child("b", SessionStatus::Idle, 2),
            child("c", SessionStatus::Failed, 3),
        ],
        None,
    );
    assert_eq!(ids(&rows), ["a", "b"]);
    let rows = active_batch(
        &mut known,
        &mut batch,
        &[
            child("a", SessionStatus::Idle, 1),
            child("b", SessionStatus::Idle, 2),
            child("c", SessionStatus::Running, 3),
        ],
        None,
    );
    assert_eq!(ids(&rows), ["a", "b", "c"]);
}

#[test]
fn batch_keeps_the_selected_child_listed() {
    let mut known = HashSet::new();
    let mut batch = HashSet::new();
    active_batch(
        &mut known,
        &mut batch,
        &[child("a", SessionStatus::Running, 1)],
        None,
    );
    // The batch ends with everything idle, but `a` is the viewed child.
    let rows = active_batch(
        &mut known,
        &mut batch,
        &[
            child("a", SessionStatus::Idle, 1),
            child("b", SessionStatus::Idle, 2),
        ],
        Some("a"),
    );
    assert_eq!(ids(&rows), ["a", "b"]);
    let rows = active_batch(
        &mut known,
        &mut batch,
        &[child("b", SessionStatus::Idle, 2)],
        Some("a"),
    );
    assert_eq!(ids(&rows), ["b"], "the vanished selected child is dropped");
}

fn ids(rows: &[PublicChildSession]) -> Vec<&str> {
    rows.iter().map(|row| row.id.as_str()).collect()
}

#[test]
fn display_name_matches_python() {
    let cases = [
        ("explore-1", "Explore 1"),
        ("general-purpose", "General Purpose"),
        ("codeRunner", "Code Runner"),
        ("HTTPServer", "HTTP Server"),
        ("already Upper", "already Upper"),
        ("UPPER", "UPPER"),
        ("foo_bar", "Foo Bar"),
        ("a1b", "A1b"),
        ("-research", "Research"),
        ("abc-", "Abc"),
    ];
    for (value, expected) in cases {
        assert_eq!(display_name(value), expected, "input: {value}");
    }
}
