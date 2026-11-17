//! Which hook notices mount, and where a tool's pre-tool hook lines sit.

use serde_json::json;
use vibe_rs::transcript::Transcript;

fn add_tool(transcript: &mut Transcript, id: &str, call: &str) {
    transcript.add(
        &json!({"entry": {"id": id, "type": "effect", "toolCallId": call,
                                     "detail": {"kind": "shell"}}}),
    );
}

fn add_pre_tool_hook(transcript: &mut Transcript, id: &str, call: &str) {
    for kind in ["hook_run_started", "hook_completed"] {
        transcript.add(
            &json!({"entry": {"id": format!("{id}-{kind}"), "type": "notice",
                                         "message": "hook",
                                         "detail": {"kind": kind, "scope": "pre_tool",
                                                    "toolCallId": call, "content": "checked",
                                                    "hookName": "guard"}}}),
        );
    }
}

fn order(transcript: &Transcript) -> Vec<String> {
    (0..transcript.entry_count())
        .map(|index| transcript.entry(index).unwrap().id.to_owned())
        .collect()
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
fn parallel_tools_sit_below_their_own_pre_tool_hook_lines() {
    // Core runs every pre-tool hook of a batch before any of its tools starts.
    let mut transcript = Transcript::default();
    add_pre_tool_hook(&mut transcript, "h1", "c1");
    add_pre_tool_hook(&mut transcript, "h2", "c2");
    add_tool(&mut transcript, "e1", "c1");
    add_tool(&mut transcript, "e2", "c2");
    assert_eq!(
        order(&transcript),
        [
            "h1-hook_run_started",
            "h1-hook_completed",
            "e1",
            "h2-hook_run_started",
            "h2-hook_completed",
            "e2"
        ]
    );
}

#[test]
fn a_pre_tool_hook_line_after_its_tool_slots_in_above_it() {
    let mut transcript = Transcript::default();
    add_tool(&mut transcript, "e1", "c1");
    add_tool(&mut transcript, "e2", "c2");
    add_pre_tool_hook(&mut transcript, "h1", "c1");
    assert_eq!(
        order(&transcript),
        ["h1-hook_run_started", "h1-hook_completed", "e1", "e2"]
    );
}

#[test]
fn a_reused_call_id_pairs_each_tool_with_its_own_hook_lines() {
    let mut transcript = Transcript::default();
    add_pre_tool_hook(&mut transcript, "h1", "c1");
    add_tool(&mut transcript, "e1", "c1");
    transcript.add(
        &json!({"entry": {"id": "m1", "type": "message", "role": "assistant",
                                     "content": [{"type": "text", "text": "again"}]}}),
    );
    add_pre_tool_hook(&mut transcript, "h2", "c1");
    add_tool(&mut transcript, "e2", "c1");
    assert_eq!(
        order(&transcript),
        [
            "h1-hook_run_started",
            "h1-hook_completed",
            "e1",
            "m1",
            "h2-hook_run_started",
            "h2-hook_completed",
            "e2"
        ]
    );
}

#[test]
fn a_tool_never_pairs_with_a_hook_line_past_a_message() {
    let mut transcript = Transcript::default();
    add_pre_tool_hook(&mut transcript, "h1", "c1");
    transcript.add(
        &json!({"entry": {"id": "m1", "type": "message", "role": "assistant",
                                     "content": [{"type": "text", "text": "next"}]}}),
    );
    add_tool(&mut transcript, "e1", "c1");
    assert_eq!(
        order(&transcript),
        ["h1-hook_run_started", "h1-hook_completed", "m1", "e1"]
    );
}

#[test]
fn a_pre_agent_hook_line_mounts_in_its_own_turn_container() {
    let mut transcript = Transcript::default();
    transcript.add(
        &json!({"entry": {"id": "n1", "type": "notice", "message": "hook",
                          "detail": {"kind": "hook_run_started", "scope": "pre_agent"}}}),
    );
    transcript.add(
        &json!({"entry": {"id": "n2", "type": "notice", "message": "hook",
                          "detail": {"kind": "hook_completed", "scope": "pre_agent",
                                     "content": "Rewrote the message",
                                     "hookName": "redact"}}}),
    );
    assert_eq!(transcript.lines().count(), 1);

    let mut other_scope = Transcript::default();
    other_scope.add(
        &json!({"entry": {"id": "n1", "type": "notice", "message": "hook",
                          "detail": {"kind": "hook_run_started", "scope": "post_agent"}}}),
    );
    other_scope.add(
        &json!({"entry": {"id": "n2", "type": "notice", "message": "hook",
                          "detail": {"kind": "hook_completed", "scope": "pre_agent",
                                     "content": "Rewrote the message",
                                     "hookName": "redact"}}}),
    );
    // A post_agent run does not open the pre_agent container.
    assert_eq!(other_scope.lines().count(), 0);
}

#[test]
fn a_model_call_hook_line_mounts_in_its_own_container() {
    for scope in ["pre_llm", "post_llm"] {
        let mut transcript = Transcript::default();
        transcript.add(
            &json!({"entry": {"id": "n1", "type": "notice", "message": "hook",
                              "detail": {"kind": "hook_run_started", "scope": scope}}}),
        );
        transcript.add(
            &json!({"entry": {"id": "n2", "type": "notice", "message": "hook",
                              "detail": {"kind": "hook_completed", "scope": scope,
                                         "content": "Asked the model to try again",
                                         "hookName": "audit"}}}),
        );
        assert_eq!(transcript.lines().count(), 1, "{scope}");
    }
}
