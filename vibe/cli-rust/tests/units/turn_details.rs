//! A settled turn's steps collapse into one `Worked for` group above its final answer.

use serde_json::{json, Value};
use vibe_rs::transcript::{Transcript, TurnDetails};

use crate::support::{message, session_state};

fn bash(id: &str) -> Value {
    json!({"id": id, "type": "effect", "title": "bash", "generationStatus": "completed"})
}

fn at(mut entry: Value, created_ms: u64) -> Value {
    entry["createdAt"] = json!(created_ms);
    entry
}

fn transcript(history: Vec<Value>) -> Transcript {
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&session_state("s", history));
    transcript
}

fn worked(prompt: &str) -> Vec<Value> {
    vec![
        message(prompt, "user", "fix it"),
        message(&format!("{prompt}-a1"), "assistant", "Looking."),
        bash(&format!("{prompt}-t1")),
        bash(&format!("{prompt}-t2")),
        message(&format!("{prompt}-a2"), "assistant", "Done."),
    ]
}

fn steer() -> Value {
    let mut steer = message("s1", "user", "also check the docs");
    steer["source"] = json!("turn_steer");
    steer
}

fn span(details: &TurnDetails) -> (usize, usize, usize, usize) {
    let TurnDetails {
        first,
        last,
        messages,
        tools,
        ..
    } = details;
    (*first, *last, *messages, *tools)
}

/// A subagent's completion notification: a hidden harness user message attached to the spawn.
fn subagent_turn() -> Vec<Value> {
    let spawn = json!({
        "id": "spawn", "type": "effect", "generationStatus": "completed",
        "detail": {"kind": "subagent", "toolName": "subagent.spawn",
            "input": {"agentName": "explore-1"}, "childSessionId": "child",
            "display": {"verb": "Exploring"}},
        "state": {"status": "completed"},
    });
    let notification = json!({
        "id": "notification", "type": "message", "role": "user", "source": "harness",
        "generationStatus": "completed",
        "content": [
            {"type": "text", "text": concat!(
                "Runtime notification:\n",
                r#"{"id":"subagent:child:explore-1:1:completed","source":{"type":"subagent","agent_name":"explore-1"}}"#
            )},
            {"type": "text", "text": "found it"},
        ],
    });
    vec![
        message("u1", "user", "explore"),
        message("a1", "assistant", "Spawning an explorer."),
        spawn,
        notification,
        message("a2", "assistant", "Reading what it found."),
        bash("t1"),
        message("a3", "assistant", "Here is the answer."),
    ]
}

#[test]
fn a_settled_turn_groups_its_steps_above_the_final_answer() {
    let details = transcript(worked("u1")).turn_details(0, false);
    let details = details.expect("the turn has details");
    assert_eq!(span(&details), (1, 3, 1, 2));
    assert_eq!(details.key, "turn-details:u1");
}

#[test]
fn the_running_turn_shows_its_steps_until_it_settles() {
    let mut history = worked("u1");
    history.extend(worked("u2"));
    let transcript = transcript(history);
    assert!(transcript.turn_details(0, true).is_some());
    assert_eq!(transcript.turn_details(5, true), None);
    assert!(transcript.turn_details(5, false).is_some());
}

#[test]
fn the_label_reads_the_time_from_the_prompt_to_the_answer() {
    let mut history = worked("u1");
    history[0] = at(history[0].clone(), 1_000);
    history[4] = at(history[4].clone(), 73_500);
    let details = transcript(history).turn_details(0, false).unwrap();
    assert_eq!(details.elapsed_ms, Some(72_500));
    assert_eq!(
        details.label(),
        "Worked for 1m 12s · 1 message · 2 tool calls"
    );
}

#[test]
fn the_label_formats_short_and_long_durations() {
    let details = |elapsed_ms| TurnDetails {
        key: String::new(),
        first: 0,
        last: 0,
        close: 0,
        outside: Vec::new(),
        messages: 0,
        tools: 1,
        elapsed_ms: Some(elapsed_ms),
    };
    assert_eq!(details(400).label(), "Worked for <1s · 1 tool call");
    assert_eq!(details(12_000).label(), "Worked for 12s · 1 tool call");
    assert_eq!(
        details(3_723_000).label(),
        "Worked for 1h 2m 3s · 1 tool call"
    );
}

#[test]
fn without_timestamps_the_label_leaves_out_the_duration() {
    let details = transcript(worked("u1")).turn_details(0, false).unwrap();
    assert_eq!(details.elapsed_ms, None);
    assert_eq!(details.label(), "Worked · 1 message · 2 tool calls");
}

#[test]
fn a_turn_with_nothing_before_its_answer_has_no_details() {
    let history = vec![
        message("u1", "user", "hi"),
        message("a1", "assistant", "Hello."),
    ];
    assert_eq!(transcript(history).turn_details(0, false), None);
}

#[test]
fn a_turn_without_an_answer_has_no_details() {
    let history = vec![message("u1", "user", "hi"), bash("t1")];
    assert_eq!(transcript(history).turn_details(0, false), None);
}

#[test]
fn only_a_prompt_opens_details() {
    assert_eq!(transcript(worked("u1")).turn_details(1, false), None);
}

#[test]
fn each_turn_ends_at_the_next_prompt() {
    let mut history = worked("u1");
    history.extend(worked("u2"));
    let transcript = transcript(history);
    assert_eq!(
        span(&transcript.turn_details(0, false).unwrap()),
        (1, 3, 1, 2)
    );
    assert_eq!(
        span(&transcript.turn_details(5, false).unwrap()),
        (6, 8, 1, 2)
    );
}

#[test]
fn a_subagent_notification_does_not_split_the_turn() {
    let details = transcript(subagent_turn()).turn_details(0, false);
    assert_eq!(details.map(|details| span(&details)), Some((1, 5, 2, 2)));
}

#[test]
fn a_steer_stays_in_the_turn_but_out_of_the_group() {
    let mut history = worked("u1");
    history.insert(3, steer());
    let transcript = transcript(history);
    assert_eq!(transcript.turn_details(3, false), None);
    let details = transcript.turn_details(0, false).unwrap();
    assert_eq!(span(&details), (1, 4, 1, 2));
    assert_eq!(details.outside, vec![3]);
    assert!(!details.contains(3) && details.contains(4));
}

#[test]
fn a_steer_does_not_settle_the_running_turn() {
    let mut history = worked("u1");
    history.insert(3, steer());
    assert_eq!(transcript(history).turn_details(0, true), None);
}

#[test]
fn prompts_sent_together_form_one_turn() {
    let mut follow_up = message("u1b", "user", "and the docs");
    follow_up["followsUser"] = json!(true);
    let mut history = worked("u1");
    history.insert(1, follow_up);
    let transcript = transcript(history);
    assert_eq!(transcript.turn_details(1, false), None);
    let details = transcript.turn_details(0, false).unwrap();
    assert_eq!(span(&details), (2, 4, 1, 2));
}

#[test]
fn a_queued_prompt_does_not_settle_the_running_turn() {
    let mut history = worked("u1");
    let mut queued = message("u2", "user", "next");
    queued["pending"] = json!(true);
    history.push(queued);
    let transcript = transcript(history);
    assert_eq!(transcript.turn_details(0, true), None);
    assert!(transcript.turn_details(0, false).is_some());
    assert_eq!(transcript.turn_details(5, false), None);
}

#[test]
fn a_running_turn_without_a_new_prompt_keeps_the_last_turn_open() {
    assert_eq!(transcript(worked("u1")).turn_details(0, true), None);
}

#[test]
fn blank_and_discarded_replies_are_not_counted() {
    let mut discarded = message("a0", "assistant", "draft");
    discarded["outcome"] = json!({"type": "discarded", "cause": "failure"});
    let mut history = worked("u1");
    history.insert(1, message("blank", "assistant", "  "));
    history.insert(1, discarded);
    history.push(message("trailing", "assistant", " "));
    let details = transcript(history).turn_details(0, false).unwrap();
    assert_eq!((details.last, details.messages, details.tools), (5, 1, 2));
}

#[test]
fn a_folded_tool_group_closes_the_group_on_its_first_call() {
    let history = vec![
        message("u1", "user", "fix it"),
        message("a1", "assistant", "Looking."),
        bash("t1"),
        bash("t2"),
        bash("t3"),
        message("a2", "assistant", "Done."),
    ];
    let transcript = transcript(history);
    let details = transcript.turn_details(0, false).unwrap();
    assert_eq!(details.last, 4);
    assert_eq!(transcript.closing_step(&details, |_| false), 2);
    assert_eq!(transcript.closing_step(&details, |_| true), 4);
}

#[test]
fn a_mid_turn_notice_folds_with_the_steps() {
    let notice = json!({
        "id": "n1", "type": "notice", "level": "warning", "message": "retrying",
        "generationStatus": "completed", "local": true,
    });
    let mut history = worked("u1");
    history.insert(2, notice);
    let details = transcript(history).turn_details(0, false).unwrap();
    assert!(details.outside.is_empty());
    assert!(details.contains(2));
}

#[test]
fn an_interrupted_turn_keeps_its_steps() {
    let mut history = worked("u1");
    history.push(json!({"id": "i1", "type": "interrupt", "local": true}));
    assert_eq!(transcript(history).turn_details(0, false), None);
}

#[test]
fn an_entry_finds_the_prompt_of_its_turn() {
    let mut history = worked("u1");
    history.insert(3, steer());
    history.extend(worked("u2"));
    let transcript = transcript(history);
    assert_eq!(transcript.turn_prompt(4), Some(0));
    assert_eq!(transcript.turn_prompt(8), Some(6));
    assert_eq!(transcript.turn_prompt(0), None);
}

#[test]
fn ctrl_o_keys_name_every_prompt() {
    let mut history = worked("u1");
    history.extend(worked("u2"));
    assert_eq!(
        transcript(history).details_keys(),
        ["turn-details:u1", "turn-details:u2"]
    );
}

#[test]
fn a_resumed_interrupted_turn_keeps_its_steps() {
    let mut cut = message("u1-a3", "assistant", "Checking the");
    cut["outcome"] = json!({"type": "discarded", "cause": "interrupt"});
    let mut history = worked("u1");
    history.push(cut);
    assert_eq!(transcript(history).turn_details(0, false), None);
}

#[test]
fn a_step_after_the_last_reply_means_no_final_answer() {
    let mut history = worked("u1");
    history.push(bash("u1-t3"));
    assert_eq!(transcript(history).turn_details(0, false), None);
}

#[test]
fn reasoning_alone_folds_under_a_bare_label() {
    let history = vec![
        message("u1", "user", "hi"),
        json!({"id": "r1", "type": "reasoning", "text": "thinking", "generationStatus": "completed"}),
        message("a1", "assistant", "Hello."),
    ];
    let mut transcript = transcript(history);
    transcript.set_show_reasoning(true);
    let details = transcript.turn_details(0, false).unwrap();
    assert_eq!((details.first, details.last), (1, 1));
    assert_eq!(details.label(), "Worked");
}

#[test]
fn rows_after_the_answer_that_are_not_model_steps_keep_the_group() {
    let model_change = json!({
        "id": "c1", "type": "checkpoint", "kind": "model_change", "generationStatus": "completed",
    });
    let status =
        json!({"id": "s1", "type": "notice", "level": "info", "message": "ok", "local": true});
    let mut history = worked("u1");
    history.extend([model_change, status]);
    assert!(transcript(history).turn_details(0, false).is_some());
}

#[test]
fn a_lone_notice_before_the_answer_forms_no_group() {
    let notice = json!({
        "id": "n1", "type": "notice", "level": "warning", "message": "retrying", "local": true,
    });
    let history = vec![
        message("u1", "user", "hi"),
        notice,
        message("a1", "assistant", "Hello."),
    ];
    assert_eq!(transcript(history).turn_details(0, false), None);
}

#[test]
fn a_bang_command_after_the_answer_keeps_the_group_and_stays_shown() {
    let bang = json!({
        "id": "x1", "type": "effect", "generationStatus": "completed",
        "detail": {"kind": "shell", "toolName": "shell", "display": {"verb": "Ran"}},
    });
    let mut history = worked("u1");
    history.insert(2, bang.clone());
    history.push(
        json!({"id": "x2", "type": "effect", "generationStatus": "completed",
        "detail": {"kind": "shell", "toolName": "shell", "display": {"verb": "Ran"}}}),
    );
    let details = transcript(history).turn_details(0, false).unwrap();
    assert_eq!(details.outside, vec![2]);
    assert_eq!((details.messages, details.tools), (1, 2));
}

#[test]
fn a_server_step_after_the_answer_means_no_group() {
    let mut history = worked("u1");
    history.push(
        json!({"id": "r9", "type": "reasoning", "text": "more", "generationStatus": "completed"}),
    );
    let mut transcript = transcript(history);
    transcript.set_show_reasoning(true);
    assert_eq!(transcript.turn_details(0, false), None);
    transcript.set_show_reasoning(false);
    assert!(transcript.turn_details(0, false).is_some());
}

#[test]
fn an_agent_shell_call_after_the_answer_means_no_group() {
    let mut history = worked("u1");
    history.push(
        json!({"id": "t9", "type": "effect", "generationStatus": "completed",
        "detail": {"kind": "shell", "toolName": "bash", "display": {"verb": "Ran"}}}),
    );
    assert_eq!(transcript(history).turn_details(0, false), None);
}
