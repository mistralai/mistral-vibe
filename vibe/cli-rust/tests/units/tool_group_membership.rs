//! Which runs fold into a tool group, and how its header counts their calls.

use serde_json::{json, Value};
use vibe_rs::transcript::Transcript;

fn effect(id: &str, state: &str) -> Value {
    effect_of_kind(id, state, Some("shell"))
}

fn effect_of_kind(id: &str, state: &str, kind: Option<&str>) -> Value {
    json!({"id": id, "type": "effect", "detail": {"kind": kind}, "state": {"status": state}})
}

fn user_message(id: &str) -> Value {
    json!({"id": id, "type": "message", "role": "user", "content": [{"type": "text", "text": "hi"}]})
}

fn assistant_text(id: &str, text: &str) -> Value {
    json!({
        "id": id,
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
    })
}

fn reasoning(id: &str) -> Value {
    json!({"id": id, "type": "reasoning", "text": "thinking"})
}

fn live(entries: &[Value]) -> Transcript {
    let mut transcript = Transcript::default();
    for entry in entries {
        transcript.add(&json!({ "entry": entry }));
    }
    transcript
}

#[test]
fn a_single_call_stays_ungrouped() {
    let transcript = live(&[
        effect("e1", "completed"),
        user_message("m1"),
        effect("e2", "completed"),
        effect("e3", "completed"),
    ]);
    assert!(transcript.entry(0).unwrap().group.is_none());
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e2".to_owned()]
    );
}

#[test]
fn a_second_call_groups_the_first() {
    let mut transcript = live(&[effect("e1", "running")]);
    assert!(transcript.entry(0).unwrap().group.is_none());
    transcript.add(&json!({"entry": effect("e2", "running")}));
    let group = transcript.entry(0).unwrap().group.expect("groups");
    assert!(group.first);
    assert_eq!(group.kinds, vec![("shell", 2)]);
}

/// Only painted rows count: a hidden thinking node leaves its call alone.
#[test]
fn a_call_groups_with_reasoning_only_while_reasoning_shows() {
    let mut transcript = live(&[reasoning("r1"), effect("e1", "completed")]);
    assert!(transcript.entry(1).unwrap().group.is_none());
    transcript.set_show_reasoning(true);
    let group = transcript.entry(1).unwrap().group.expect("groups");
    assert!(group.reasoning);
    assert_eq!(group.kinds, vec![("shell", 1)]);
}

#[test]
fn unknown_and_missing_kinds_count_as_one_tool_label() {
    let transcript = live(&[
        effect_of_kind("e1", "completed", Some("cron")),
        effect_of_kind("e2", "completed", Some("scratchpad")),
        effect_of_kind("e3", "completed", None),
    ]);
    let group = transcript.group_of("e1").expect("groups");
    assert_eq!(group.kinds, vec![("tool", 3)]);
    assert_eq!(group.label(false), "Called 3 tools");
}

#[test]
fn interleaved_unknown_kinds_share_the_first_tool_label() {
    let transcript = live(&[
        effect_of_kind("e1", "completed", Some("tool")),
        effect_of_kind("e2", "completed", Some("file_read")),
        effect_of_kind("e3", "completed", Some("tool")),
        effect_of_kind("e4", "completed", Some("shell")),
        effect_of_kind("e5", "completed", Some("todo")),
    ]);
    let group = transcript.group_of("e1").expect("groups");
    assert_eq!(
        group.label(false),
        "Called 2 tools, read 1 file, ran 1 command, updated todos 1 time"
    );
}

#[test]
fn kinds_sharing_a_verb_merge_into_one_segment() {
    let transcript = live(&[
        effect_of_kind("e1", "completed", Some("shell")),
        effect_of_kind("e2", "completed", Some("file_read")),
        effect_of_kind("e3", "completed", Some("file_search")),
        effect_of_kind("e4", "completed", Some("shell")),
        effect_of_kind("e5", "completed", Some("web_search")),
    ]);
    let group = transcript.group_of("e1").expect("groups");
    assert_eq!(
        group.label(false),
        "Ran 2 commands, 1 search and 1 web search, read 1 file"
    );
    assert_eq!(
        group.label(true),
        "Running 2 commands, 1 search and 1 web search, reading 1 file"
    );
}

#[test]
fn assistant_text_still_splits_call_rounds() {
    let transcript = live(&[
        effect("e1", "completed"),
        effect("e2", "completed"),
        assistant_text("m1", "Working on it."),
        effect("e3", "completed"),
        effect("e4", "completed"),
    ]);
    assert_eq!(
        transcript.expandable_ids(),
        vec!["tool-group:e1".to_owned(), "tool-group:e3".to_owned()]
    );
}
