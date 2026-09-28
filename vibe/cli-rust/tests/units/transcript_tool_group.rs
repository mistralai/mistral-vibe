//! Live tool-group tail semantics (Python `ToolGroup` finalize lifecycle).

use serde_json::json;
use vibe_rs::server::PublicSessionState;
use vibe_rs::transcript::Transcript;

fn effect(id: &str, state: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "effect",
        "detail": {"kind": "shell", "display": {"verb": "Running"}},
        "state": {"status": state},
    })
}

fn user_message(id: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "message",
        "role": "user",
        "content": [{"type": "text", "text": "hi"}],
    })
}

/// Live entries, reduced one `history/entryAdded` at a time.
fn live(entries: &[serde_json::Value]) -> Transcript {
    let mut transcript = Transcript::default();
    for entry in entries {
        transcript.add(&json!({ "entry": entry }));
    }
    transcript
}

/// The same history mounted by a rebuild (Python `build_history_widgets`).
fn rebuilt(entries: &[serde_json::Value]) -> Transcript {
    let state = json!({
        "eventId": 0,
        "session": {"id": "s"},
        "history": entries,
    });
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&serde_json::from_value::<PublicSessionState>(state).unwrap());
    transcript
}

/// Whether the first entry's group still spins.
fn running(transcript: &Transcript) -> bool {
    !transcript
        .entry(0)
        .unwrap()
        .group
        .expect("first entry groups")
        .finalized
}

#[test]
fn trailing_group_keeps_running_after_a_successful_turn() {
    // A successful turn is a no-op for the live tail (Python
    // `_finalize_turn_ui` never finalizes the group), so it keeps spinning.
    let tail = live(&[effect("e1", "completed"), effect("e2", "completed")]);
    assert!(running(&tail));
}

#[test]
fn trailing_group_settles_on_a_failed_or_interrupted_turn() {
    // Python `stop_current_tool_call` finalizes the open group on turn error.
    let mut tail = live(&[effect("e1", "completed")]);
    tail.finalize_tool_group();
    assert!(!running(&tail));
}

#[test]
fn trailing_group_settles_on_a_later_non_groupable_entry() {
    // Python `_handle_entry_added` finalizes the group when the new entry
    // cannot join it, and a groupable entry afterwards opens the next one.
    let closed = live(&[effect("e1", "completed"), user_message("m1")]);
    assert!(!running(&closed));
    let reopened = live(&[
        effect("e1", "completed"),
        user_message("m1"),
        effect("e2", "completed"),
    ]);
    assert!(!running(&reopened));
    assert!(!reopened.entry(2).unwrap().group.expect("groups").finalized);
}

#[test]
fn trailing_group_settles_on_a_rebuild() {
    // A persisted history ending on tool calls (CLI killed mid-turn) must
    // settle on resume, like Python's post-loop `current_group.finalize()`.
    let history = [effect("e1", "completed"), effect("e2", "completed")];
    assert!(running(&live(&history)));
    assert!(!running(&rebuilt(&history)));
    assert_eq!(
        rebuilt(&history).expandable_ids(),
        vec!["tool-group:e1".to_owned()]
    );
}
