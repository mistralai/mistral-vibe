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
