//! Local transcript rows keep their position across server snapshot rebuilds.

use serde_json::{json, Value};
use vibe_rs::server::PublicSessionState;
use vibe_rs::transcript::Transcript;

fn entry(id: &str, local: bool) -> Value {
    json!({
        "id": id,
        "type": "message",
        "role": if local { "command" } else { "assistant" },
        "content": [{"type": "text", "text": id}],
        "generationStatus": "completed",
        "local": local,
    })
}

fn state(history: Vec<Value>) -> PublicSessionState {
    serde_json::from_value(json!({
        "eventId": 1,
        "session": {"id": "session-1"},
        "history": history,
    }))
    .unwrap()
}

fn transcript() -> Transcript {
    let mut transcript = Transcript::default();
    for item in [
        entry("local-prefix", true),
        entry("server-first", false),
        entry("local-middle", true),
        entry("server-second", false),
        entry("local-suffix", true),
    ] {
        transcript.add(&json!({"entry": item}));
    }
    transcript
}

fn assert_order(transcript: &Transcript) {
    let ids = transcript.lines().map(|entry| entry.id).collect::<Vec<_>>();
    assert_eq!(
        ids,
        [
            "local-prefix",
            "server-first",
            "local-middle",
            "server-second",
            "local-suffix",
        ]
    );
}

#[test]
fn full_snapshot_appends_local_entries_after_foreign_history() {
    let mut transcript = transcript();
    transcript.load_snapshot(&state(vec![
        entry("foreign-first", false),
        entry("foreign-second", false),
    ]));
    let ids = transcript.lines().map(|entry| entry.id).collect::<Vec<_>>();
    assert_eq!(
        ids,
        [
            "foreign-first",
            "foreign-second",
            "local-prefix",
            "local-middle",
            "local-suffix",
        ]
    );
}

#[test]
fn live_snapshot_preserves_local_entry_positions() {
    let mut transcript = transcript();
    transcript.load_live_snapshot(&state(vec![
        entry("server-first", false),
        entry("server-second", false),
    ]));
    assert_order(&transcript);
}
