//! A settled turn's steps collapse into `Worked for` groups, one per segment between the user's rows.

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

/// The turn's only group, if it forms exactly one.
fn single(transcript: &Transcript, prompt: usize, running: bool) -> Option<TurnDetails> {
    let mut groups = transcript.turn_details(prompt, running);
    assert!(groups.len() <= 1, "expected at most one group: {groups:?}");
    groups.pop()
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
    let details = single(&transcript(worked("u1")), 0, false);
    let details = details.expect("the turn has details");
    assert_eq!(span(&details), (1, 3, 1, 2));
    assert_eq!(details.key, "turn-details:u1");
}

#[test]
fn the_running_turn_shows_its_steps_until_it_settles() {
    let mut history = worked("u1");
    history.extend(worked("u2"));
    let transcript = transcript(history);
    assert!(single(&transcript, 0, true).is_some());
    assert_eq!(single(&transcript, 5, true), None);
    assert!(single(&transcript, 5, false).is_some());
}

#[test]
fn the_label_reads_the_time_from_the_prompt_to_the_answer() {
    let mut history = worked("u1");
    history[0] = at(history[0].clone(), 1_000);
    history[4] = at(history[4].clone(), 73_500);
    let details = single(&transcript(history), 0, false).unwrap();
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
    let details = single(&transcript(worked("u1")), 0, false).unwrap();
    assert_eq!(details.elapsed_ms, None);
    assert_eq!(details.label(), "Worked · 1 message · 2 tool calls");
}

#[test]
fn a_turn_with_nothing_before_its_answer_has_no_details() {
    let history = vec![
        message("u1", "user", "hi"),
        message("a1", "assistant", "Hello."),
    ];
    assert_eq!(single(&transcript(history), 0, false), None);
}

#[test]
fn a_turn_without_an_answer_has_no_details() {
    let history = vec![message("u1", "user", "hi"), bash("t1")];
    assert_eq!(single(&transcript(history), 0, false), None);
}

#[test]
fn only_a_prompt_returns_the_turns_groups() {
    assert_eq!(single(&transcript(worked("u1")), 1, false), None);
}

#[test]
fn each_turn_ends_at_the_next_prompt() {
    let mut history = worked("u1");
    history.extend(worked("u2"));
    let transcript = transcript(history);
    assert_eq!(span(&single(&transcript, 0, false).unwrap()), (1, 3, 1, 2));
    assert_eq!(span(&single(&transcript, 5, false).unwrap()), (6, 8, 1, 2));
}

#[test]
fn a_subagent_notification_does_not_split_the_turn() {
    let details = single(&transcript(subagent_turn()), 0, false);
    assert_eq!(details.map(|details| span(&details)), Some((1, 5, 2, 2)));
}

#[test]
fn a_steer_splits_the_turn_into_a_group_before_it_and_one_after_it() {
    let mut history = worked("u1");
    history.insert(3, steer());
    history[0] = at(history[0].clone(), 1_000);
    history[3] = at(history[3].clone(), 11_000);
    history[5] = at(history[5].clone(), 41_000);
    let transcript = transcript(history);
    assert!(transcript.turn_details(3, false).is_empty());
    let groups = transcript.turn_details(0, false);
    let spans: Vec<_> = groups.iter().map(span).collect();
    assert_eq!(spans, [(1, 2, 1, 1), (4, 4, 0, 1)]);
    let keys: Vec<_> = groups.iter().map(|group| group.key.as_str()).collect();
    assert_eq!(keys, ["turn-details:u1", "turn-details:s1"]);
    let elapsed: Vec<_> = groups.iter().map(|group| group.elapsed_ms).collect();
    assert_eq!(elapsed, [Some(10_000), Some(30_000)]);
    assert!(groups.iter().all(|group| !group.contains(3)));
}

#[test]
fn a_steer_right_before_the_answer_opens_no_group() {
    let mut history = worked("u1");
    history.insert(4, steer());
    let groups = transcript(history).turn_details(0, false);
    let spans: Vec<_> = groups.iter().map(span).collect();
    assert_eq!(spans, [(1, 3, 1, 2)]);
}

#[test]
fn a_steer_does_not_settle_the_running_turn() {
    let mut history = worked("u1");
    history.insert(3, steer());
    assert_eq!(single(&transcript(history), 0, true), None);
}

#[test]
fn prompts_sent_together_form_one_turn() {
    let mut follow_up = message("u1b", "user", "and the docs");
    follow_up["followsUser"] = json!(true);
    let mut history = worked("u1");
    history.insert(1, follow_up);
    let transcript = transcript(history);
    assert_eq!(single(&transcript, 1, false), None);
    let details = single(&transcript, 0, false).unwrap();
    assert_eq!(span(&details), (2, 4, 1, 2));
}

#[test]
fn a_queued_prompt_does_not_settle_the_running_turn() {
    let mut history = worked("u1");
    let mut queued = message("u2", "user", "next");
    queued["pending"] = json!(true);
    history.push(queued);
    let transcript = transcript(history);
    assert_eq!(single(&transcript, 0, true), None);
    assert!(single(&transcript, 0, false).is_some());
    assert_eq!(single(&transcript, 5, false), None);
}

#[test]
fn a_running_turn_without_a_new_prompt_keeps_the_last_turn_open() {
    assert_eq!(single(&transcript(worked("u1")), 0, true), None);
}

#[test]
fn blank_and_discarded_replies_are_not_counted() {
    let mut discarded = message("a0", "assistant", "draft");
    discarded["outcome"] = json!({"type": "discarded", "cause": "failure"});
    let mut history = worked("u1");
    history.insert(1, message("blank", "assistant", "  "));
    history.insert(1, discarded);
    history.push(message("trailing", "assistant", " "));
    let details = single(&transcript(history), 0, false).unwrap();
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
    let details = single(&transcript, 0, false).unwrap();
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
    let details = single(&transcript(history), 0, false).unwrap();
    assert!(details.contains(2));
}

#[test]
fn an_interrupted_turn_keeps_its_steps() {
    let mut history = worked("u1");
    history.push(json!({"id": "i1", "type": "interrupt", "local": true}));
    assert_eq!(single(&transcript(history), 0, false), None);
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
fn ctrl_o_keys_name_every_prompt_and_steer() {
    let mut history = worked("u1");
    history.insert(3, steer());
    history.extend(worked("u2"));
    assert_eq!(
        transcript(history).details_keys(),
        ["turn-details:u1", "turn-details:s1", "turn-details:u2"]
    );
}

#[test]
fn a_resumed_interrupted_turn_keeps_its_steps() {
    let mut cut = message("u1-a3", "assistant", "Checking the");
    cut["outcome"] = json!({"type": "discarded", "cause": "interrupt"});
    let mut history = worked("u1");
    history.push(cut);
    assert_eq!(single(&transcript(history), 0, false), None);
}

#[test]
fn a_step_after_the_last_reply_means_no_final_answer() {
    let mut history = worked("u1");
    history.push(bash("u1-t3"));
    assert_eq!(single(&transcript(history), 0, false), None);
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
    let details = single(&transcript, 0, false).unwrap();
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
    assert!(single(&transcript(history), 0, false).is_some());
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
    assert_eq!(single(&transcript(history), 0, false), None);
}

#[test]
fn a_bang_command_splits_the_steps_and_one_after_the_answer_keeps_them() {
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
    let groups = transcript(history).turn_details(0, false);
    let spans: Vec<_> = groups.iter().map(span).collect();
    assert_eq!(spans, [(1, 1, 1, 0), (3, 4, 0, 2)]);
}

#[test]
fn a_server_step_after_the_answer_means_no_group() {
    let mut history = worked("u1");
    history.push(
        json!({"id": "r9", "type": "reasoning", "text": "more", "generationStatus": "completed"}),
    );
    let mut transcript = transcript(history);
    transcript.set_show_reasoning(true);
    assert_eq!(single(&transcript, 0, false), None);
    transcript.set_show_reasoning(false);
    assert!(single(&transcript, 0, false).is_some());
}

#[test]
fn an_agent_shell_call_after_the_answer_means_no_group() {
    let mut history = worked("u1");
    history.push(
        json!({"id": "t9", "type": "effect", "generationStatus": "completed",
        "detail": {"kind": "shell", "toolName": "bash", "display": {"verb": "Ran"}}}),
    );
    assert_eq!(single(&transcript(history), 0, false), None);
}
