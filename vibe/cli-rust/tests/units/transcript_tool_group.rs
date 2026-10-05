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

fn assistant_text(id: &str, text: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
    })
}

fn answered_callback(id: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "callback",
        "callbackId": id,
        "title": "Tool approval",
        "detail": {"kind": "tool_approval", "request": {}},
        "state": {"status": "answered"},
        "generationStatus": "completed",
    })
}

fn server_notice(id: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "notice",
        "level": "info",
        "message": "Session title updated",
        "detail": {"kind": "session_title_updated", "title": "t"},
    })
}

fn scheduled_loop_notice(id: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "notice",
        "level": "info",
        "message": "Loop fired",
        "detail": {"kind": "scheduled_loop_fired", "loopId": "l1"},
    })
}

fn agent_change(id: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "checkpoint",
        "kind": "agent_change",
        "details": {"agent": "plan"},
    })
}

fn unknown_entry(id: &str) -> serde_json::Value {
    json!({"id": id, "type": "future_kind"})
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

/// A widget-less entry between two call rounds must not open a second block:
/// every call lands in the same collapsed group (VIBE-4587).
#[test]
fn answered_callback_keeps_call_rounds_in_one_group() {
    let transcript = live(&[
        effect("e1", "completed"),
        answered_callback("cb1"),
        effect("e2", "completed"),
    ]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned()]
    );
    assert!(transcript.entry(0).unwrap().group.unwrap().first);
    assert!(transcript.entry(2).unwrap().group.unwrap().last);
}

#[test]
fn widget_less_server_notice_keeps_call_rounds_in_one_group() {
    let transcript = live(&[
        effect("e1", "completed"),
        server_notice("n1"),
        effect("e2", "completed"),
    ]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned()]
    );
}

#[test]
fn assistant_text_still_splits_call_rounds() {
    let transcript = live(&[
        effect("e1", "completed"),
        assistant_text("m1", "Working on it."),
        effect("e2", "completed"),
    ]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned(), "tool-group:e2".to_owned()]
    );
}

#[test]
fn unknown_entry_keeps_call_rounds_in_one_group() {
    let transcript = live(&[
        effect("e1", "completed"),
        unknown_entry("u1"),
        effect("e2", "completed"),
    ]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned()]
    );
}

#[test]
fn agent_change_keeps_call_rounds_in_one_group() {
    let transcript = live(&[
        effect("e1", "completed"),
        agent_change("c1"),
        effect("e2", "completed"),
    ]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned()]
    );
}

/// The group's header and border land on the first and last rows that paint,
/// so a widget-less entry may also open or close the run.
#[test]
fn widget_less_entry_first_keeps_header_on_first_call() {
    let transcript = live(&[answered_callback("cb1"), effect("e1", "completed")]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned()]
    );
    let group = transcript.entry(1).unwrap().group.unwrap();
    assert!(group.first);
    assert!(group.last);
}

#[test]
fn widget_less_entry_last_keeps_border_on_last_call() {
    let transcript = live(&[effect("e1", "completed"), answered_callback("cb1")]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned()]
    );
    let group = transcript.entry(0).unwrap().group.unwrap();
    assert!(group.first);
    assert!(group.last);
}

#[test]
fn scheduled_loop_notice_does_not_split_call_rounds() {
    // The notice paints nothing: the fired loop shows on its prompt instead.
    let transcript = live(&[
        effect("e1", "completed"),
        scheduled_loop_notice("n1"),
        effect("e2", "completed"),
    ]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned()]
    );
}

#[test]
fn scheduled_loop_prompt_splits_call_rounds() {
    let transcript = live(&[
        effect("e1", "completed"),
        user_message("u1"),
        scheduled_loop_notice("n1"),
        effect("e2", "completed"),
    ]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned(), "tool-group:e2".to_owned()]
    );
}

#[test]
fn local_notice_still_splits_call_rounds() {
    let mut notice = server_notice("n1");
    notice["local"] = true.into();
    let transcript = live(&[effect("e1", "completed"), notice, effect("e2", "completed")]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned(), "tool-group:e2".to_owned()]
    );
}
