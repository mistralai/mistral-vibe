//! Goal notices paint one line each, and a continuation's notice heads its turn's output.

use serde_json::{json, Value};
use vibe_rs::goal::format::{notice_parts, NoticeTone};
use vibe_rs::server::NoticeDetail;

use crate::support::live;

fn notice(id: &str, turn: Option<&str>, detail: Value) -> Value {
    json!({
        "id": id, "type": "notice", "turnId": turn, "level": "info",
        "message": "Goal blocked", "detail": detail,
    })
}

fn goal_detail(event: &str) -> Value {
    json!({
        "kind": "goal", "event": event, "goalId": "g", "objective": null,
        "status": "active", "statusReason": null, "continuation": 2,
        "tokensUsed": 12_500, "tokenBudget": 50_000, "timeUsedSeconds": 3_900,
    })
}

fn parts(event: &str, objective: Option<&str>) -> (&'static str, NoticeTone, String) {
    let mut value = goal_detail(event);
    value["objective"] = json!(objective);
    let detail: NoticeDetail = serde_json::from_value(value).unwrap();
    let (glyph, tone, parts) = notice_parts(&detail, "Goal paused: turn interrupted");
    (glyph, tone, parts.join(" · "))
}

fn assistant(id: &str, turn: &str) -> Value {
    json!({"id": id, "type": "message", "role": "assistant", "turnId": turn,
        "content": [{"type": "text", "text": "working"}]})
}

#[test]
fn each_event_reads_as_one_line() {
    let accent = NoticeTone::Accent;
    assert_eq!(
        parts("set", Some("Ship it")),
        ("◆", accent, "Goal set · Ship it".into())
    );
    assert_eq!(parts("edited", None), ("◆", accent, "Goal edited".into()));
    assert_eq!(parts("cleared", None), ("◆", accent, "Goal cleared".into()));
    assert_eq!(
        parts("continued", None),
        (
            "◆",
            accent,
            "Working on the goal · continuation 2 · 12.5K / 50K tokens".into()
        )
    );
    assert_eq!(
        parts("completed", Some("Ship it")),
        (
            "✓",
            NoticeTone::Success,
            "Goal complete · 12.5K tokens · 1h05m · Ship it".into()
        )
    );
    assert_eq!(
        parts("completed", None),
        (
            "✓",
            NoticeTone::Success,
            "Goal complete · 12.5K tokens · 1h05m".into()
        )
    );
    assert_eq!(
        parts("interrupted", None),
        (
            "!",
            NoticeTone::Warning,
            "Goal paused: turn interrupted".into()
        )
    );
}

#[test]
fn a_goal_that_spent_nothing_shows_no_token_count() {
    let mut value = goal_detail("continued");
    value["continuation"] = json!(1);
    value["tokensUsed"] = json!(0);
    value["tokenBudget"] = Value::Null;
    let detail: NoticeDetail = serde_json::from_value(value).unwrap();
    let (_, _, parts) = notice_parts(&detail, "Working on the goal");
    assert_eq!(parts.join(" · "), "Working on the goal · continuation 1");
}

fn effect(id: &str) -> Value {
    json!({"id": id, "type": "effect", "state": {"status": "completed"},
        "detail": {"kind": "shell", "display": {"verb": "Running"}}})
}

#[test]
fn a_goal_notice_is_a_row_between_tool_groups() {
    let transcript = live(&[
        effect("e1"),
        effect("e2"),
        notice("n1", None, goal_detail("set")),
        effect("e3"),
        effect("e4"),
    ]);
    assert_eq!(
        transcript.expandable_ids(),
        ["tool-group:e1".to_owned(), "tool-group:e3".to_owned()]
    );
    let ids: Vec<_> = transcript
        .lines()
        .map(|entry| entry.id.to_owned())
        .collect();
    assert!(ids.contains(&"n1".to_owned()));
}

#[test]
fn other_server_notices_still_fold_into_the_group() {
    let title = notice("t", None, json!({"kind": "session_title_updated"}));
    let transcript = live(&[effect("e1"), title, effect("e2")]);
    assert_eq!(transcript.expandable_ids(), ["tool-group:e1".to_owned()]);
}

#[test]
fn a_late_continuation_notice_moves_above_its_turns_output() {
    let transcript = live(&[
        assistant("a0", "t0"),
        assistant("a1", "t1"),
        notice("n1", Some("t1"), goal_detail("continued")),
        assistant("a2", "t1"),
    ]);
    let ids: Vec<_> = transcript
        .lines()
        .map(|entry| entry.id.to_owned())
        .collect();
    assert_eq!(ids, ["a0", "n1", "a1", "a2"]);
}

#[test]
fn other_goal_notices_stay_where_they_arrive() {
    let transcript = live(&[
        assistant("a1", "t1"),
        notice("n1", Some("t1"), goal_detail("completed")),
    ]);
    let ids: Vec<_> = transcript
        .lines()
        .map(|entry| entry.id.to_owned())
        .collect();
    assert_eq!(ids, ["a1", "n1"]);
}

fn user(id: &str, turn: &str) -> Value {
    json!({"id": id, "type": "message", "role": "user", "turnId": turn,
        "content": [{"type": "text", "text": "next"}]})
}

fn ids(entries: &[Value]) -> Vec<String> {
    live(entries)
        .lines()
        .map(|entry| entry.id.to_owned())
        .collect()
}

#[test]
fn a_late_stop_notice_follows_its_turn_not_the_next_prompt() {
    let entries = [
        assistant("a1", "t1"),
        user("u2", "t2"),
        assistant("a2", "t2"),
        notice("n1", Some("t1"), goal_detail("completed")),
    ];
    assert_eq!(ids(&entries), ["a1", "n1", "u2", "a2"]);
}

#[test]
fn every_stop_event_anchors_after_its_turn() {
    for event in [
        "blocked",
        "budget_limited",
        "usage_limited",
        "interrupted",
        "paused",
    ] {
        let entries = [
            assistant("a1", "t1"),
            user("u2", "t2"),
            notice("n1", Some("t1"), goal_detail(event)),
        ];
        assert_eq!(ids(&entries), ["a1", "n1", "u2"], "{event}");
    }
}

#[test]
fn a_stop_notice_without_a_turn_or_a_known_turn_appends() {
    let entries = [
        assistant("a1", "t1"),
        user("u2", "t2"),
        notice("n1", None, goal_detail("completed")),
        notice("n2", Some("gone"), goal_detail("completed")),
    ];
    assert_eq!(ids(&entries), ["a1", "u2", "n1", "n2"]);
}

#[test]
fn a_non_stop_notice_with_a_turn_appends() {
    let entries = [
        assistant("a1", "t1"),
        user("u2", "t2"),
        notice("n1", Some("t1"), goal_detail("resumed")),
    ];
    assert_eq!(ids(&entries), ["a1", "u2", "n1"]);
}
