//! A fired scheduled loop annotates the user prompt it sent instead of rendering itself.

use serde_json::json;
use vibe_rs::transcript::Transcript;
use vibe_rs::utils::datetime::format_utc;

fn user_message(id: &str, turn: &str, text: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "message",
        "role": "user",
        "turnId": turn,
        "content": [{"type": "text", "text": text}],
    })
}

fn fired(id: &str, turn: &str) -> serde_json::Value {
    json!({
        "id": id,
        "type": "notice",
        "turnId": turn,
        "createdAt": 1_787_593_260_000u64,
        "level": "info",
        "message": "Loop `abc` fired",
        "detail": {"kind": "scheduled_loop_fired", "loopId": "abc"},
    })
}

fn live(entries: &[serde_json::Value]) -> Transcript {
    let mut transcript = Transcript::default();
    for entry in entries {
        transcript.add(&json!({ "entry": entry }));
    }
    transcript
}

#[test]
fn fired_loop_annotates_its_prompt_and_hides_the_notice() {
    let transcript = live(&[
        user_message("u0", "t0", "earlier"),
        user_message("u1", "t1", "Run the linter"),
        fired("n1", "t1"),
    ]);

    let rendered: Vec<_> = transcript.lines().collect();
    assert_eq!(
        rendered.iter().map(|entry| entry.id).collect::<Vec<_>>(),
        ["u0", "u1"]
    );
    assert!(rendered[0].fired_loop.is_none());
    let fired = rendered[1].fired_loop.expect("prompt carries its loop");
    assert_eq!(fired.loop_id, "abc");
    assert_eq!(fired.fired_at, Some(1_787_593_260_000));
}

#[test]
fn forked_prompt_keeps_its_loop_from_the_display_marker() {
    let mut prompt = user_message("imported-1", "t1", "Run the linter");
    prompt["turnId"] = serde_json::Value::Null;
    prompt["userDisplayContent"] = json!({
        "version": "1",
        "host": "vibe",
        "content": [{"type": "vibe.scheduled_loop", "loopId": "abc", "firedAt": 42}],
    });
    let transcript = live(&[prompt]);

    let rendered: Vec<_> = transcript.lines().collect();
    let fired = rendered[0].fired_loop.expect("marker restores the loop");
    assert_eq!(fired.loop_id, "abc");
    assert_eq!(fired.fired_at, Some(42));
    assert!(transcript.user_messages().is_empty());
}

#[test]
fn rewind_skips_prompts_a_loop_sent() {
    let transcript = live(&[
        user_message("u0", "t0", "earlier"),
        user_message("u1", "t1", "Run the linter"),
        fired("n1", "t1"),
    ]);

    let ids: Vec<_> = transcript
        .user_messages()
        .into_iter()
        .map(|(_, id, _)| id)
        .collect();
    assert_eq!(ids, ["u0"]);
}

#[test]
fn fired_loop_without_a_same_turn_prompt_renders_nothing() {
    let transcript = live(&[user_message("u0", "t0", "earlier"), fired("n1", "t1")]);

    let rendered: Vec<_> = transcript.lines().collect();
    assert_eq!(
        rendered.iter().map(|entry| entry.id).collect::<Vec<_>>(),
        ["u0"]
    );
    assert!(rendered[0].fired_loop.is_none());
}

#[test]
fn utc_format_matches_python_strftime() {
    assert_eq!(format_utc(1_787_593_260_000), "2026-08-24 17:41:00 UTC");
    assert_eq!(format_utc(0), "1970-01-01 00:00:00 UTC");
    assert_eq!(format_utc(951_782_400_000), "2000-02-29 00:00:00 UTC");
}
