//! Session histories prepare bottom-up without paging or moving old scroll state.

use serde_json::{json, Value};
use vibe_rs::transcript::Transcript;
use vibe_rs::utils::transcript_cache::TranscriptCache;

use crate::support::session_state;

fn message(index: usize) -> Value {
    json!({"id": format!("message-{index}"), "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": format!("Message {index}")}]})
}

#[test]
fn all_components_prepare_automatically_from_the_bottom() {
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&session_state("saved", (0..53).map(message).collect()));
    let revisions: Vec<_> = transcript
        .lines()
        .map(|entry| (entry.id.to_owned(), entry.rev))
        .collect();
    let mut cache = TranscriptCache::default();
    cache.start_history(transcript.entry_count());
    let mut starts = Vec::new();
    while cache.advance_history(&transcript) {
        starts.push(cache.history_from());
        assert_eq!(
            transcript
                .lines_from(cache.history_from())
                .last()
                .unwrap()
                .id,
            "message-52"
        );
    }
    assert_eq!(starts, [28, 3, 0]);
    assert!(!cache.preparing_history());
    assert_eq!(
        transcript
            .lines()
            .map(|entry| (entry.id.to_owned(), entry.rev))
            .collect::<Vec<_>>(),
        revisions
    );
}

#[test]
fn preparation_restarts_for_a_different_history() {
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&session_state("first", (0..100).map(message).collect()));
    let mut cache = TranscriptCache::default();
    cache.start_history(transcript.entry_count());
    assert!(cache.advance_history(&transcript));
    assert_eq!(cache.history_from(), 75);
    transcript.load_snapshot(&session_state("second", (0..3).map(message).collect()));
    cache.start_history(transcript.entry_count());
    assert!(cache.advance_history(&transcript));
    assert_eq!(cache.history_from(), 0);
    assert!(!cache.advance_history(&transcript));
}

#[test]
fn preparing_an_empty_history_finishes_immediately() {
    let transcript = Transcript::default();
    let mut cache = TranscriptCache::default();
    cache.start_history(0);
    assert!(!cache.advance_history(&transcript));
    assert!(!cache.preparing_history());
}

#[test]
fn a_tool_group_is_never_split_from_its_header() {
    let mut history = vec![message(0)];
    history.extend((0..40).map(|index| {
        json!({
            "id": format!("tool-{index}"), "type": "effect", "detail": {"kind": "shell"},
            "state": {"status": "completed"}
        })
    }));
    history.extend((41..81).map(message));
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&session_state("saved", history));
    let mut cache = TranscriptCache::default();
    cache.start_history(transcript.entry_count());
    assert!(cache.advance_history(&transcript));
    assert_eq!(cache.history_from(), 56);
    assert!(cache.advance_history(&transcript));
    assert_eq!(cache.history_from(), 1);
    let first = transcript.lines_from(cache.history_from()).next().unwrap();
    assert_eq!(first.id, "tool-0");
    assert!(first.group.unwrap().first);
}
