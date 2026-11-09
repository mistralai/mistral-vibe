//! Older history pages prepend above the transcript and track the paging cursor.

use serde_json::{json, Value};
use vibe_rs::older_history::parse_page;
use vibe_rs::server::PublicSessionState;
use vibe_rs::transcript::{HistoryCursor, Transcript};
use vibe_rs::utils::transcript_cache::{EntryGeometry, TranscriptCache};

fn message(index: usize) -> Value {
    json!({"id": format!("message-{index}"), "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": format!("Message {index}")}]})
}

fn state(id: &str, history: Vec<Value>, before: Option<&str>) -> PublicSessionState {
    serde_json::from_value(
        json!({"eventId": 1, "session": {"id": id}, "history": history,
        "historyBeforeCursor": before}),
    )
    .unwrap()
}

fn cursor(before: &str) -> HistoryCursor {
    HistoryCursor {
        session_id: "saved".into(),
        before: before.into(),
    }
}

fn ids(transcript: &Transcript) -> Vec<String> {
    transcript
        .lines()
        .map(|entry| entry.id.to_owned())
        .collect()
}

fn loaded() -> Transcript {
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&state(
        "saved",
        (4..6).map(message).collect(),
        Some("message-4"),
    ));
    transcript
}

#[test]
fn a_resumed_page_keeps_its_cursor() {
    assert_eq!(loaded().history_before_cursor(), Some(&cursor("message-4")));
    let mut complete = Transcript::default();
    complete.load_snapshot(&state("saved", vec![message(0)], None));
    assert_eq!(complete.history_before_cursor(), None);
}

#[test]
fn pages_prepend_in_order_until_the_first_entry() {
    let mut transcript = loaded();
    let added = transcript.prepend_history(
        &cursor("message-4"),
        (2..4).map(message).collect(),
        Some("message-2".into()),
    );
    assert_eq!(added, Some(2));
    assert_eq!(
        transcript.history_before_cursor(),
        Some(&cursor("message-2"))
    );
    let added =
        transcript.prepend_history(&cursor("message-2"), (0..2).map(message).collect(), None);
    assert_eq!(added, Some(2));
    assert_eq!(transcript.history_before_cursor(), None);
    let expected: Vec<String> = (0..6).map(|index| format!("message-{index}")).collect();
    assert_eq!(ids(&transcript), expected);
    assert_eq!(transcript.entry(5).unwrap().id, "message-5");
}

#[test]
fn a_stale_answer_is_ignored() {
    let mut transcript = loaded();
    let added = transcript.prepend_history(&cursor("message-9"), vec![message(0)], None);
    assert_eq!(added, None);
    assert_eq!(transcript.entry_count(), 2);
    assert_eq!(
        transcript.history_before_cursor(),
        Some(&cursor("message-4"))
    );
}

#[test]
fn an_empty_or_duplicate_page_ends_paging() {
    let mut transcript = loaded();
    let added = transcript.prepend_history(
        &cursor("message-4"),
        vec![message(4)],
        Some("message-4".into()),
    );
    assert_eq!(added, Some(0));
    assert_eq!(transcript.history_before_cursor(), None);
    assert_eq!(transcript.entry_count(), 2);
}

#[test]
fn a_live_snapshot_keeps_the_paged_prefix_and_its_cursor() {
    let mut transcript = loaded();
    transcript.prepend_history(
        &cursor("message-4"),
        (2..4).map(message).collect(),
        Some("message-2".into()),
    );
    transcript.load_live_snapshot(&state(
        "saved",
        (5..7).map(message).collect(),
        Some("message-5"),
    ));
    assert_eq!(transcript.entry_count(), 5);
    assert_eq!(
        transcript.history_before_cursor(),
        Some(&cursor("message-2"))
    );
    transcript.load_live_snapshot(&state(
        "saved",
        (8..10).map(message).collect(),
        Some("message-8"),
    ));
    assert_eq!(transcript.entry_count(), 2);
    assert_eq!(
        transcript.history_before_cursor(),
        Some(&cursor("message-8"))
    );
}

#[test]
fn the_cursor_follows_snapshots_and_clears() {
    let mut transcript = loaded();
    let saved = transcript.snapshot();
    transcript.load_snapshot(&state("other", vec![message(0)], None));
    transcript.restore(saved);
    assert_eq!(
        transcript.history_before_cursor(),
        Some(&cursor("message-4"))
    );
    transcript.clear();
    assert_eq!(transcript.history_before_cursor(), None);
}

#[test]
fn shifted_entries_keep_their_cached_heights() {
    let mut transcript = loaded();
    let rev = transcript.entry(1).unwrap().rev;
    let mut cache = TranscriptCache::default();
    cache.ensure_context(80, 79, 0, false);
    let geometry = EntryGeometry {
        height: 7,
        prewrapped: false,
    };
    cache.geometry(1, rev, 80, || geometry);
    transcript.prepend_history(&cursor("message-4"), (2..4).map(message).collect(), None);
    cache.start_older_history(2);
    assert_eq!(transcript.entry(3).unwrap().rev, rev);
    let cached = cache.geometry(3, rev, 80, || unreachable!("height was cached"));
    assert_eq!(cached.height, 7);
    assert_eq!(cache.history_from(), 2);
    assert!(cache.advance_history(&transcript));
    assert_eq!(cache.history_from(), 0);
}

fn tool(id: &str) -> Value {
    json!({"id": id, "type": "effect", "detail": {"kind": "shell"}, "state": {"status": "completed"}})
}

#[test]
fn a_tool_group_across_the_seam_measures_again() {
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&state(
        "saved",
        vec![tool("tool-2"), message(3), message(4)],
        Some("tool-2"),
    ));
    let revs: Vec<u64> = (0..3)
        .map(|index| transcript.entry(index).unwrap().rev)
        .collect();
    transcript.prepend_history(&cursor("tool-2"), vec![message(0), tool("tool-1")], None);
    assert_ne!(transcript.entry(2).unwrap().rev, revs[0]);
    assert!(!transcript.entry(2).unwrap().group.unwrap().first);
    assert!(transcript.entry(1).unwrap().group.unwrap().first);
    assert_ne!(transcript.entry(3).unwrap().rev, revs[1]);
    assert_eq!(transcript.entry(4).unwrap().rev, revs[2]);
}

#[test]
fn a_reply_without_items_is_a_failure() {
    assert!(parse_page(&json!({"nextCursor": "message-2"})).is_none());
    assert!(parse_page(&json!({})).is_none());
}

#[test]
fn a_reply_with_items_parses_its_cursor() {
    let page = parse_page(&json!({"items": [message(0)], "nextCursor": "message-0"})).unwrap();
    assert_eq!(page.items, vec![message(0)]);
    assert_eq!(page.next_cursor.as_deref(), Some("message-0"));
    let page = parse_page(&json!({"items": [], "nextCursor": null})).unwrap();
    assert!(page.items.is_empty());
    assert_eq!(page.next_cursor, None);
}
