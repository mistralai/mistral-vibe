//! Server compaction checkpoints: tool-group splitting and settling once their turn is over.

use serde_json::json;
use vibe_rs::server::{HistoryEntry, PublicSessionState};
use vibe_rs::transcript::{HistoryCursor, Transcript};

fn effect(id: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "effect",
        "detail": {"kind": "shell", "display": {"verb": "Running"}},
        "state": {"status": "completed"},
    })
}

fn compaction(id: &str, trigger: &str, status: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "checkpoint",
        "turnId": "t1",
        "kind": "compaction",
        "generationStatus": status,
        "message": "Compacting context",
        "details": {"trigger": trigger, "attempt": 1},
    })
}

fn live(entries: &[serde_json::Value]) -> Transcript {
    let mut transcript = Transcript::default();
    for entry in entries {
        transcript.add(&json!({ "entry": entry }));
    }
    transcript
}

/// The same history mounted by a resume whose state reports `turns`.
fn resumed(entries: &[serde_json::Value], turns: serde_json::Value) -> Transcript {
    let state = json!({
        "eventId": 0,
        "session": {"id": "s"},
        "history": entries,
        "turns": turns,
    });
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&serde_json::from_value::<PublicSessionState>(state).unwrap());
    transcript
}

fn groups(trigger: &str) -> Vec<Vec<String>> {
    let history = [
        effect("e1"),
        effect("e2"),
        compaction("c1", trigger, "completed"),
        effect("e3"),
        effect("e4"),
    ];
    vec![
        live(&history).expandable_ids(),
        resumed(&history, json!([])).expandable_ids(),
    ]
}

/// The checkpoint at `index`: whether it is in progress, its details and message.
fn checkpoint(transcript: &Transcript, index: usize) -> (bool, serde_json::Value, String) {
    let entry = transcript.entry(index).unwrap().entry;
    let HistoryEntry::Checkpoint(checkpoint) = entry else {
        panic!("entry {index} is not a checkpoint");
    };
    let message = checkpoint.message.clone().unwrap_or_default();
    (entry.in_progress(), checkpoint.details.clone(), message)
}

fn assert_interrupted(transcript: &Transcript, index: usize) {
    let (in_progress, details, message) = checkpoint(transcript, index);
    assert!(!in_progress);
    assert_eq!(message, "Context compaction interrupted");
    assert_eq!(details["trigger"], "automatic");
    assert_eq!(details["error"]["code"], "interrupted");
}

#[test]
fn automatic_compaction_splits_call_rounds() {
    for ids in groups("automatic") {
        assert_eq!(
            ids,
            vec!["tool-group:e1".to_owned(), "tool-group:e3".to_owned()]
        );
    }
}

#[test]
fn manual_compaction_keeps_call_rounds_in_one_group() {
    for ids in groups("manual") {
        assert_eq!(ids, vec!["tool-group:e1".to_owned()]);
    }
}

#[test]
fn turn_end_settles_every_open_compaction_as_interrupted() {
    let mut transcript = live(&[
        compaction("c1", "automatic", "in_progress"),
        compaction("c2", "automatic", "in_progress"),
    ]);
    assert!(checkpoint(&transcript, 0).0);
    transcript.settle_compactions(None);
    assert_interrupted(&transcript, 0);
    assert_interrupted(&transcript, 1);
}

#[test]
fn settling_keeps_the_running_turn_compaction() {
    let mut transcript = live(&[compaction("c1", "automatic", "in_progress")]);
    transcript.settle_compactions(Some("t1"));
    assert!(checkpoint(&transcript, 0).0);
}

#[test]
fn settling_leaves_a_settled_compaction_alone() {
    let mut transcript = live(&[compaction("c1", "automatic", "completed")]);
    transcript.settle_compactions(None);
    let (_, details, message) = checkpoint(&transcript, 0);
    assert_eq!(message, "Compacting context");
    assert!(details.get("error").is_none());
}

#[test]
fn resume_settles_a_compaction_no_running_turn_owns() {
    let history = [compaction("c1", "automatic", "in_progress")];
    assert_interrupted(
        &resumed(&history, json!([{"id": "t1", "status": "interrupted"}])),
        0,
    );
    let running = resumed(&history, json!([{"id": "t1", "status": "in_progress"}]));
    assert!(checkpoint(&running, 0).0);
}

#[test]
fn an_older_page_settles_its_open_compaction() {
    let state = json!({
        "eventId": 0,
        "session": {"id": "s"},
        "history": [effect("e1")],
        "historyBeforeCursor": "e1",
        "turns": [{"id": "t9", "status": "in_progress"}],
    });
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&serde_json::from_value::<PublicSessionState>(state).unwrap());
    let cursor = HistoryCursor {
        session_id: "s".into(),
        before: "e1".into(),
    };
    let page = vec![compaction("c1", "automatic", "in_progress")];
    assert_eq!(transcript.prepend_history(&cursor, page, None), Some(1));
    assert_interrupted(&transcript, 0);
}
