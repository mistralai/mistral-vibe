//! JSON-Patch application for transcript updates and entry-group projections.

use serde_json::{json, Value};
use vibe_rs::server::HistoryEntry;
use vibe_rs::transcript::{apply_json_patch, effect_kind, groups_with_tools, Group, Transcript};

#[test]
fn add_and_replace_reach_nested_paths_and_create_missing_parents() {
    let mut root = json!({"a": {"b": 1}, "items": ["x", "y"]});
    apply_json_patch(
        &mut root,
        &[
            json!({"op": "add", "path": "/a/b", "value": 2}),
            json!({"op": "add", "path": "/a/c", "value": "leaf"}),
            json!({"op": "replace", "path": "/items/1", "value": "z"}),
        ],
    );
    assert_eq!(root["a"]["b"], json!(2));
    assert_eq!(root["a"]["c"], json!("leaf"));
    assert_eq!(root["items"], json!(["x", "z"]));
}

#[test]
fn add_through_a_missing_grandparent_is_skipped() {
    let mut root = json!({"a": {}});
    apply_json_patch(
        &mut root,
        &[json!({"op": "add", "path": "/a/c/d", "value": 1})],
    );
    assert_eq!(root["a"]["c"]["d"], Value::Null);
}

#[test]
fn add_unescapes_json_pointer_tokens() {
    let mut root = json!({});
    apply_json_patch(
        &mut root,
        &[json!({"op": "add", "path": "/a~1b", "value": 1})],
    );
    assert_eq!(root["a/b"], json!(1));
}

#[test]
fn append_grows_strings_and_arrays_only() {
    let mut root = json!({"text": "Par", "list": [1]});
    apply_json_patch(
        &mut root,
        &[
            json!({"op": "append", "path": "/text", "value": "tial"}),
            json!({"op": "append", "path": "/list", "value": 2}),
        ],
    );
    assert_eq!(root["text"], json!("Partial"));
    assert_eq!(root["list"], json!([1, 2]));
}

#[test]
fn append_to_a_number_is_skipped() {
    let mut root = json!({"count": 1});
    apply_json_patch(
        &mut root,
        &[json!({"op": "append", "path": "/count", "value": 2})],
    );
    assert_eq!(root["count"], json!(1));
}

#[test]
fn remove_drops_nested_and_root_object_keys() {
    let mut root = json!({"a": {"b": {"c": 1}}, "top": 2});
    apply_json_patch(
        &mut root,
        &[
            json!({"op": "remove", "path": "/a/b/c"}),
            json!({"op": "remove", "path": "/top"}),
        ],
    );
    assert!(root["a"]["b"].as_object().unwrap().is_empty());
    assert!(root.get("top").is_none());
}

#[test]
fn test_op_is_a_noop_even_when_the_value_differs() {
    let mut root = json!({"keep": 1});
    apply_json_patch(
        &mut root,
        &[json!({"op": "test", "path": "/keep", "value": 999})],
    );
    assert_eq!(root["keep"], json!(1));
}

#[test]
fn unknown_ops_and_unresolvable_paths_are_skipped() {
    let mut root = json!({"scalar": 1, "arr": [0]});
    apply_json_patch(
        &mut root,
        &[
            json!({"op": "move", "path": "/scalar"}),
            json!({"op": "frobnicate", "path": "/scalar"}),
            json!({"op": "add", "path": "/scalar/leaf", "value": 1}),
            json!({"op": "add", "path": "/arr/5", "value": 1}),
            json!({"op": "add", "value": 1}),
        ],
    );
    assert_eq!(root["scalar"], json!(1));
    assert_eq!(root["arr"], json!([0]));
}

#[test]
fn transcript_update_applies_patch_ops_and_bumps_the_event_id() {
    let mut transcript = Transcript::default();
    transcript.add(&json!({
        "entry": {"id": "m1", "type": "message", "role": "assistant",
                  "content": [{"type": "text", "text": "Par"}]},
        "eventId": 1
    }));
    transcript.update(&json!({
        "entryId": "m1",
        "patch": [{"op": "append", "path": "/content/0/text", "value": "tial"},
                  {"op": "add", "path": "extra", "value": "field"}],
        "eventId": 2
    }));
    assert_eq!(
        transcript.last_assistant_message().as_deref(),
        Some("Partial")
    );
    assert_eq!(transcript.last_event_id, 2);
}

#[test]
fn transcript_update_for_an_unknown_entry_is_ignored() {
    let mut transcript = Transcript::default();
    transcript.add(&json!({
        "entry": {"id": "m1", "type": "message", "role": "assistant",
                  "content": [{"type": "text", "text": "Par"}]},
        "eventId": 1
    }));
    transcript.update(&json!({
        "entryId": "missing",
        "patch": [{"op": "append", "path": "/content/0/text", "value": "tial"}],
        "eventId": 2
    }));
    assert_eq!(transcript.last_assistant_message().as_deref(), Some("Par"));
}

#[test]
fn consecutive_effects_share_one_group_keyed_by_the_first_entry() {
    let mut transcript = Transcript::default();
    transcript.add(&json!({"entry": {"id": "e1", "type": "effect",
                                     "detail": {"kind": "file_edit"}}}));
    transcript.add(&json!({"entry": {"id": "e2", "type": "effect",
                                     "detail": {"kind": "file_search"}}}));
    let first = transcript.entry(0).unwrap().group.expect("first groups");
    let second = transcript.entry(1).unwrap().group.expect("second groups");
    assert_eq!(first.key, "tool-group:e1");
    assert_eq!(second.key, "tool-group:e1");
    assert!(first.first && !first.last);
    assert!(!second.first && second.last);
    assert_eq!(first.kinds, vec![("file_edit", 1), ("file_search", 1)]);
    assert!(transcript.is_expandable("tool-group:e1"));
}

#[test]
fn a_message_between_effects_starts_a_new_group() {
    let mut transcript = Transcript::default();
    transcript.add(&json!({"entry": {"id": "e1", "type": "effect",
                                     "detail": {"kind": "file_edit"}}}));
    transcript.add(&json!({"entry": {"id": "e1b", "type": "effect",
                                     "detail": {"kind": "file_edit"}}}));
    assert!(transcript.entry(1).unwrap().group.expect("groups").last);
    transcript.add(
        &json!({"entry": {"id": "m1", "type": "message", "role": "user",
                                     "content": [{"type": "text", "text": "hi"}]}}),
    );
    for id in ["e2", "e3"] {
        transcript.add(&json!({"entry": {"id": id, "type": "effect",
                                         "detail": {"kind": "file_search"}}}));
    }
    let second_group = transcript.entry(3).unwrap().group.expect("groups");
    assert_eq!(second_group.key, "tool-group:e2");
}

#[test]
fn shell_effects_stay_standalone() {
    let mut transcript = Transcript::default();
    transcript.add(&json!({"entry": {"id": "s1", "type": "effect",
                                     "detail": {"kind": "shell", "toolName": "shell"}}}));
    assert!(transcript.entry(0).unwrap().group.is_none());
}

#[test]
fn skill_effects_group_and_expand_like_python() {
    let mut transcript = Transcript::default();
    for id in ["skill-1", "skill-2"] {
        transcript.add(&json!({"entry": {
            "id": id,
            "type": "effect",
            "detail": {"kind": "skill", "toolName": "skill"},
            "state": {"status": "completed", "outputText": "private body"},
        }}));
    }

    assert!(transcript.entry(0).unwrap().group.is_some());
    assert!(transcript.is_expandable("skill-1"));
}

#[test]
fn hidden_entries_do_not_render_until_reasoning_is_enabled() {
    let mut transcript = Transcript::default();
    transcript.add(&json!({"entry": {"id": "r1", "type": "reasoning", "text": "thinking"}}));
    transcript.add(
        &json!({"entry": {"id": "n1", "type": "notice", "message": "hook",
                                     "detail": {"kind": "hook_run_started"}}}),
    );
    transcript.add(
        &json!({"entry": {"id": "m1", "type": "message", "role": "user",
                                     "content": [{"type": "text", "text": "hi"}]}}),
    );
    assert_eq!(transcript.lines().count(), 1);
    transcript.set_show_reasoning(true);
    assert_eq!(transcript.lines().count(), 2);
}

#[test]
fn hook_notices_mount_only_inside_an_open_container() {
    let mut transcript = Transcript::default();
    transcript.add(
        &json!({"entry": {"id": "n1", "type": "notice", "message": "hook",
                          "detail": {"kind": "hook_run_started", "scope": "pre_tool",
                                     "toolCallId": "t1"}}}),
    );
    transcript.add(
        &json!({"entry": {"id": "n2", "type": "notice", "message": "hook",
                          "detail": {"kind": "hook_completed", "scope": "pre_tool",
                                     "toolCallId": "t1", "content": "ran lint",
                                     "hookName": "lint"}}}),
    );
    // The lifecycle line is hidden; only the completed line with content mounts.
    assert_eq!(transcript.lines().count(), 1);

    let mut orphan = Transcript::default();
    orphan.add(
        &json!({"entry": {"id": "n2", "type": "notice", "message": "hook",
                          "detail": {"kind": "hook_completed", "scope": "pre_tool",
                                     "toolCallId": "t1", "content": "ran lint",
                                     "hookName": "lint"}}}),
    );
    // A completed hook outside any opened container renders nothing.
    assert_eq!(orphan.lines().count(), 0);
}

#[test]
fn groups_with_tools_covers_groupable_entries() {
    let reasoning = HistoryEntry::from_value(&json!({"type": "reasoning", "text": "t"}));
    let hook = HistoryEntry::from_value(&json!({"type": "notice",
                                                "detail": {"kind": "hook_completed"}}));
    let shell = HistoryEntry::from_value(&json!({"type": "effect",
                                                 "detail": {"kind": "shell", "toolName": "shell"}}));
    let skill = HistoryEntry::from_value(&json!({"type": "effect",
                                                 "detail": {"kind": "skill"}}));
    let other = HistoryEntry::from_value(&json!({"type": "effect",
                                                 "detail": {"kind": "file_read"}}));
    let subagent = HistoryEntry::from_value(&json!({"type": "effect",
                                                    "detail": {"kind": "subagent"}}));
    let question = HistoryEntry::from_value(&json!({"type": "effect",
                                                    "detail": {"kind": "user_question"}}));
    let message = HistoryEntry::from_value(&json!({"type": "message"}));
    assert!(groups_with_tools(&reasoning));
    assert!(groups_with_tools(&hook));
    assert!(groups_with_tools(&skill));
    assert!(groups_with_tools(&other));
    assert!(groups_with_tools(&subagent));
    assert!(!groups_with_tools(&shell));
    assert!(!groups_with_tools(&question));
    assert!(!groups_with_tools(&message));
}

#[test]
fn effect_kind_reads_only_effect_details() {
    let effect = HistoryEntry::from_value(&json!({"type": "effect",
                                                  "detail": {"kind": "file_edit"}}));
    let message = HistoryEntry::from_value(&json!({"type": "message"}));
    assert_eq!(effect_kind(&effect), Some("file_edit"));
    assert_eq!(effect_kind(&message), None);
}

fn group_of(kinds: Vec<(&'static str, usize)>, reasoning: bool) -> Group<'static> {
    Group {
        key: "tool-group:e1".into(),
        first: true,
        last: true,
        finalized: true,
        kinds,
        reasoning,
    }
}

#[test]
fn group_labels_count_each_kind_then_add_reasoning_and_capitalize() {
    let group = group_of(vec![("file_edit", 3), ("web_search", 1)], false);
    assert_eq!(group.label(false), "Edited 3 files, ran 1 web search");
    assert_eq!(group.label(true), "Editing 3 files, running 1 web search");

    let thinking = group_of(vec![("subagent", 2)], true);
    assert_eq!(thinking.label(false), "Ran 2 subagents, thought");
    assert_eq!(thinking.label(true), "Running 2 subagents, thinking");
}

#[test]
fn group_labels_pluralize_by_count() {
    let group = group_of(
        vec![("file_read", 10), ("skill", 1), ("shell", 3), ("todo", 2)],
        false,
    );
    assert_eq!(
        group.label(false),
        "Read 10 files, loaded 1 skill, ran 3 commands, updated todos 2 times"
    );
}

#[test]
fn unknown_kinds_fall_back_to_generic_tool_labels() {
    let group = group_of(vec![("custom_tool", 2)], false);
    assert_eq!(group.label(false), "Called 2 tools");
    assert_eq!(group.label(true), "Calling 2 tools");
}
