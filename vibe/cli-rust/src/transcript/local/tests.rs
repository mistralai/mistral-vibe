use serde_json::{json, Value};

use super::{add_pending_prompt, promote_prompts};
use crate::server::{HistoryEntry, MessageContent, PublicSessionState};
use crate::transcript::Transcript;

fn server(id: &str, role: &str) -> Value {
    json!({"entry": {"id": id, "type": "message", "role": role,
        "content": [{"type": "text", "text": id}], "generationStatus": "completed"}})
}

fn state(history: Vec<Value>) -> PublicSessionState {
    let history: Vec<Value> = history
        .into_iter()
        .map(|item| item["entry"].clone())
        .collect();
    serde_json::from_value(json!({"eventId": 1, "session": {"id": "s"}, "history": history}))
        .unwrap()
}

#[test]
fn merged_prompt_does_not_reappear_below_newer_messages_after_snapshot() {
    let mut transcript = Transcript::default();
    add_pending_prompt(&mut transcript, "queued", "first prompt");
    add_pending_prompt(&mut transcript, "merged", "note: second prompt");
    transcript.add(&server("queued", "user"));
    promote_prompts(&mut transcript, &["queued".into(), "merged".into()]);
    for (id, role) in [
        ("answer", "assistant"),
        ("latest", "user"),
        ("partial", "assistant"),
    ] {
        transcript.add(&server(id, role));
    }

    transcript.load_live_snapshot(&state(vec![
        server("latest", "user"),
        server("retry", "assistant"),
    ]));

    let ids: Vec<&str> = transcript.lines().map(|entry| entry.id).collect();
    assert_eq!(ids, ["latest", "retry"]);
}

fn merged_head() -> Value {
    json!({"entry": {"id": "queued", "type": "message", "role": "user",
        "content": [{"type": "text", "text": "first prompt\n\nnote: second prompt"}],
        "generationStatus": "completed"}})
}

fn promoted_merged_run() -> Transcript {
    let mut transcript = Transcript::default();
    transcript.add(&server("old", "user"));
    add_pending_prompt(&mut transcript, "queued", "first prompt");
    add_pending_prompt(&mut transcript, "merged", "note: second prompt");
    promote_prompts(&mut transcript, &["queued".into(), "merged".into()]);
    transcript
}

fn shown(transcript: &Transcript) -> Vec<(String, String)> {
    transcript
        .lines()
        .map(|line| {
            let HistoryEntry::Message(message) = line.entry else {
                return (line.id.to_owned(), String::new());
            };
            let text = message
                .content
                .iter()
                .filter_map(|block| match block {
                    MessageContent::Text { text } => Some(text.as_str()),
                    _ => None,
                })
                .collect();
            (line.id.to_owned(), text)
        })
        .collect()
}

#[test]
fn merged_follow_up_survives_snapshot_taken_before_head_echo() {
    let mut transcript = promoted_merged_run();

    transcript.load_live_snapshot(&state(vec![server("old", "user")]));
    transcript.add(&merged_head());

    assert_eq!(
        shown(&transcript),
        [
            ("old".to_owned(), "old".to_owned()),
            ("queued".to_owned(), "first prompt".to_owned()),
            ("merged".to_owned(), "note: second prompt".to_owned()),
        ]
    );
}

#[test]
fn merged_follow_up_is_not_duplicated_when_snapshot_has_merged_head() {
    let mut transcript = promoted_merged_run();

    transcript.load_live_snapshot(&state(vec![server("old", "user"), merged_head()]));

    assert_eq!(
        shown(&transcript),
        [
            ("old".to_owned(), "old".to_owned()),
            (
                "queued".to_owned(),
                "first prompt\n\nnote: second prompt".to_owned()
            ),
        ]
    );
}

#[test]
fn a_huge_compaction_error_settles_capped() {
    use crate::utils::text::{MESSAGE_MAX_CHARS, MESSAGE_TRUNCATION_MARKER};
    let mut transcript = Transcript::default();
    super::add_compact_status(&mut transcript, "compact");

    super::settle_compact_status(&mut transcript, "compact", Some(&"A".repeat(1_200_000)));

    assert_eq!(
        shown(&transcript),
        vec![(
            "compact".to_owned(),
            format!(
                "Error: {}{MESSAGE_TRUNCATION_MARKER}",
                "A".repeat(MESSAGE_MAX_CHARS)
            )
        )]
    );
}

#[test]
fn a_huge_error_row_is_capped_whatever_its_caller() {
    use crate::utils::text::{MESSAGE_TRUNCATION_MARKER, ROW_MAX_CHARS};
    let mut transcript = Transcript::default();

    super::add_command_error(&mut transcript, "error", &"A".repeat(1_200_000));

    assert_eq!(
        shown(&transcript),
        vec![(
            "error".to_owned(),
            format!("{}{MESSAGE_TRUNCATION_MARKER}", "A".repeat(ROW_MAX_CHARS))
        )]
    );
}

#[test]
fn interrupt_marker_stays_above_the_next_prompt_when_a_snapshot_drops_rows() {
    let mut transcript = Transcript::default();
    for id in ["old", "older-page", "cut-off"] {
        transcript.add(&server(id, "assistant"));
    }
    super::add_interrupt(&mut transcript, "marker");
    transcript.add(&server("next", "user"));
    transcript.add(&server("tool", "assistant"));

    transcript.load_live_snapshot(&state(vec![
        server("older-page", "assistant"),
        server("next", "user"),
        server("tool", "assistant"),
    ]));

    let ids: Vec<&str> = transcript.lines().map(|entry| entry.id).collect();
    assert_eq!(ids, ["older-page", "marker", "next", "tool"]);
}

#[test]
fn a_row_whose_next_anchor_is_gone_goes_right_below_the_server_row_above_it() {
    let mut transcript = Transcript::default();
    transcript.add(&server("a", "user"));
    super::add_interrupt(&mut transcript, "marker");
    transcript.add(&server("b", "user"));
    super::add_command_status(&mut transcript, "status", "Done");
    transcript.add(&server("streaming", "assistant"));

    transcript.load_live_snapshot(&state(vec![
        server("y", "user"),
        server("z", "assistant"),
        server("a", "user"),
        server("b", "user"),
    ]));

    let ids: Vec<&str> = transcript.lines().map(|entry| entry.id).collect();
    assert_eq!(ids, ["y", "z", "a", "marker", "b", "status"]);
}

#[test]
fn a_row_without_both_anchors_stays_below_the_rows_restored_before_it() {
    let mut transcript = Transcript::default();
    transcript.add(&server("a", "user"));
    super::add_interrupt(&mut transcript, "marker");
    transcript.add(&server("b", "user"));
    transcript.add(&server("b2", "assistant"));
    super::add_command_status(&mut transcript, "status", "Done");
    transcript.add(&server("streaming", "assistant"));

    transcript.load_live_snapshot(&state(vec![
        server("p1", "user"),
        server("p2", "assistant"),
        server("p3", "user"),
        server("p4", "assistant"),
        server("b", "user"),
    ]));

    let ids: Vec<&str> = transcript.lines().map(|entry| entry.id).collect();
    assert_eq!(ids, ["p1", "p2", "p3", "p4", "marker", "b", "status"]);
}
