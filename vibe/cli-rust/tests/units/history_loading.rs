//! Session histories prepare bottom-up without paging or moving old scroll state.

use std::sync::Arc;

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::resume_picker::{self, Event};
use vibe_rs::server::{Client, PublicSessionState};
use vibe_rs::transcript::Transcript;
use vibe_rs::utils::transcript_cache::TranscriptCache;

fn message(index: usize) -> Value {
    json!({"id": format!("message-{index}"), "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": format!("Message {index}")}]})
}

fn state(id: &str, history: Vec<Value>) -> PublicSessionState {
    serde_json::from_value(json!({"eventId": 1, "session": {"id": id}, "history": history}))
        .unwrap()
}

#[test]
fn all_components_prepare_automatically_from_the_bottom() {
    let mut transcript = Transcript::default();
    transcript.load_snapshot(&state("saved", (0..53).map(message).collect()));
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
    transcript.load_snapshot(&state("first", (0..100).map(message).collect()));
    let mut cache = TranscriptCache::default();
    cache.start_history(transcript.entry_count());
    assert!(cache.advance_history(&transcript));
    assert_eq!(cache.history_from(), 75);
    transcript.load_snapshot(&state("second", (0..3).map(message).collect()));
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
    transcript.load_snapshot(&state("saved", history));
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

#[test]
fn accepting_a_preview_starts_at_the_bottom_even_mid_preparation() {
    let mut app = App::default();
    app.resume_picker.open = true;
    app.resume_picker.preview_request = 2;
    app.view
        .transcript
        .load_snapshot(&state("old", (0..100).map(message).collect()));
    app.view.transcript_cache.start_history(100);
    app.view.scroll = 20;
    app.view.scroll_target = 40;
    app.view.last_total = 200;
    app.view.scroll_to_entry = Some(5);
    resume_picker::apply_event(
        &mut app,
        &Arc::new(Client::stub()),
        Event::Preview {
            request: 2,
            state: state("new", (0..3).map(message).collect()),
        },
    );
    assert_eq!(app.view.scroll, 0);
    assert_eq!(app.view.scroll_target, 0);
    assert_eq!(app.view.last_total, 0);
    assert_eq!(app.view.scroll_to_entry, None);
    assert_eq!(app.view.transcript.entry_count(), 3);
    assert_eq!(app.view.transcript_cache.history_from(), 3);
}

#[test]
fn a_stale_preview_does_not_replace_or_reset_the_selected_session() {
    let mut app = App::default();
    app.resume_picker.open = true;
    app.resume_picker.preview_request = 2;
    app.view
        .transcript
        .load_snapshot(&state("selected", vec![message(99)]));
    app.view.scroll = 20;
    app.view.scroll_target = 20;
    resume_picker::apply_event(
        &mut app,
        &Arc::new(Client::stub()),
        Event::Preview {
            request: 1,
            state: state("old", vec![message(0)]),
        },
    );
    assert_eq!(app.view.transcript.entry(0).unwrap().id, "message-99");
    assert_eq!(app.view.scroll, 20);
    assert_eq!(app.view.scroll_target, 20);
}

#[test]
fn reopening_on_the_current_session_rejects_the_previous_picker_reply() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.session.session_id = Some("current".into());
    app.resume_picker.preview_request = 1;
    app.view
        .transcript
        .load_snapshot(&state("current", vec![message(42)]));
    resume_picker::open(&mut app, &client);
    resume_picker::apply_event(
        &mut app,
        &client,
        Event::Loaded(vec![resume_picker::Session {
            id: "current".into(),
            ..Default::default()
        }]),
    );
    resume_picker::apply_event(
        &mut app,
        &client,
        Event::Preview {
            request: 1,
            state: state("old", vec![message(0)]),
        },
    );
    assert_eq!(app.view.transcript.entry(0).unwrap().id, "message-42");
}

#[test]
fn reopening_the_picker_cannot_accept_an_old_preview() {
    let client = Arc::new(Client::stub());
    let mut app = App::default();
    app.resume_picker.preview_request = 1;
    app.view
        .transcript
        .load_snapshot(&state("current", vec![message(42)]));
    resume_picker::open(&mut app, &client);
    resume_picker::apply_event(
        &mut app,
        &client,
        Event::Loaded(vec![resume_picker::Session {
            id: "new".into(),
            ..Default::default()
        }]),
    );
    resume_picker::apply_event(
        &mut app,
        &client,
        Event::Preview {
            request: 1,
            state: state("old", vec![message(0)]),
        },
    );
    assert_eq!(app.view.transcript.entry(0).unwrap().id, "message-42");
}
