use super::*;

#[test]
fn start_turn_bumps_generation_and_reserves_fresh_data() {
    let mut tracker = TurnSummaryTracker::default();
    tracker.start_turn("first");
    tracker.track_assistant_text("partial");
    tracker.track_user_message("m1");
    assert_eq!(tracker.generation(), 1);
    tracker.start_turn("second");
    assert_eq!(tracker.generation(), 2);
    let (data, generation) = tracker.end_turn().unwrap();
    assert_eq!(generation, 2);
    assert_eq!(data.user_message, "second");
    assert!(data.assistant_fragments.is_empty());
    assert_eq!(data.message_id, None);
    assert_eq!(data.error, None);
}

#[test]
fn assistant_fragments_join_in_order() {
    let mut tracker = TurnSummaryTracker::default();
    tracker.start_turn("hello");
    tracker.track_assistant_text("Hello ");
    tracker.track_assistant_text("big ");
    tracker.track_assistant_text("world");
    tracker.track_assistant_text("");
    let (data, _) = tracker.end_turn().unwrap();
    assert_eq!(data.assistant_text(), "Hello big world");
}

#[test]
fn track_user_message_and_error_land_in_the_data() {
    let mut tracker = TurnSummaryTracker::default();
    tracker.start_turn("hello");
    tracker.track_user_message("m1");
    tracker.set_error("boom");
    let (data, _) = tracker.end_turn().unwrap();
    assert_eq!(data.message_id.as_deref(), Some("m1"));
    assert_eq!(data.error.as_deref(), Some("boom"));
}

#[test]
fn cancel_turn_clears_data_and_end_turn_consumes_it() {
    let mut tracker = TurnSummaryTracker::default();
    tracker.start_turn("hello");
    tracker.track_assistant_text("text");
    tracker.cancel_turn();
    assert!(tracker.end_turn().is_none());
    tracker.start_turn("again");
    assert!(tracker.end_turn().is_some());
    assert!(tracker.end_turn().is_none());
}

#[test]
fn feed_handlers_without_a_turn_are_noops() {
    let mut tracker = TurnSummaryTracker::default();
    tracker.track_user_message("m1");
    tracker.track_assistant_text("text");
    tracker.set_error("boom");
    assert!(tracker.end_turn().is_none());
}

#[test]
fn disabled_narrator_accumulates_nothing() {
    let mut app = App::default();
    app.session.startup_config.narrator_enabled = false;
    on_turn_start(&mut app, "hello");
    on_assistant_text(&mut app, "text");
    on_user_message(&mut app, "m1");
    on_turn_error(&mut app, "boom");
    assert!(app.narrator.summary.end_turn().is_none());
    assert_eq!(app.narrator.summary.generation(), 1);
}

#[test]
fn cancel_interrupts_summarizing_and_answer_settles_idle() {
    let mut app = App::default();
    app.session.startup_config.narrator_enabled = true;
    on_turn_start(&mut app, "hello");
    // `on_turn_end` needs a live client, so enter `Summarizing` directly.
    app.narrator.state = NarratorState::Summarizing;
    assert!(cancel(&mut app));
    assert_eq!(app.narrator.state, NarratorState::Idle);
    assert!(!cancel(&mut app));
    app.narrator.state = NarratorState::Summarizing;
    let generation = app.narrator.summary.generation();
    apply_event(
        &mut app,
        Event::Summary {
            generation,
            summary: Some("done".into()),
        },
    );
    assert_eq!(app.narrator.state, NarratorState::Idle);
}

#[test]
fn stale_generation_summary_is_ignored() {
    let mut app = App::default();
    app.session.startup_config.narrator_enabled = true;
    on_turn_start(&mut app, "first");
    on_turn_start(&mut app, "second");
    apply_event(
        &mut app,
        Event::Summary {
            generation: 1,
            summary: Some("stale".into()),
        },
    );
    assert_eq!(app.narrator.state, NarratorState::Idle);
    // The current generation's summary also settles the row silently.
    apply_event(
        &mut app,
        Event::Summary {
            generation: 2,
            summary: None,
        },
    );
    assert_eq!(app.narrator.state, NarratorState::Idle);
}
