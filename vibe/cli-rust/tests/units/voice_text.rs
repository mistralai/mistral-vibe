//! Dictated text lands at the caret and the caret follows it.

use vibe_rs::app::App;
use vibe_rs::voice::VoiceEvent;

#[test]
fn dictated_text_advances_the_caret() {
    let mut app = App::default();

    app.apply_voice_event(VoiceEvent::TextDelta("hello".to_owned()));
    app.apply_voice_event(VoiceEvent::TextDelta(" world".to_owned()));

    assert_eq!(app.chat_input.input, "hello world");
    assert_eq!(app.chat_input.cursor, "hello world".len());
}

#[test]
fn dictated_text_is_inserted_at_the_caret() {
    let mut app = App::default();
    app.chat_input.input = "ab".to_owned();
    app.chat_input.cursor = 1;
    app.chat_input.anchor = Some(0);

    app.apply_voice_event(VoiceEvent::TextDelta("X".to_owned()));

    assert_eq!(app.chat_input.input, "aXb");
    assert_eq!(app.chat_input.cursor, 2);
    assert_eq!(app.chat_input.anchor, None);
}

#[test]
fn dictated_text_leaves_the_recalled_history_entry() {
    let mut app = App::default();
    app.chat_input.input = "recalled".to_owned();
    app.chat_input.cursor = app.chat_input.input.len();
    app.chat_input.cursor_pos_after_load = Some(app.chat_input.cursor);

    app.apply_voice_event(VoiceEvent::TextDelta(" more".to_owned()));

    assert_eq!(app.chat_input.cursor_pos_after_load, None);
    assert!(!app.chat_input.cursor_moved_since_load);
}

#[test]
fn dictated_text_refreshes_the_completion_popup() {
    let mut app = App::default();

    app.apply_voice_event(VoiceEvent::TextDelta("/he".to_owned()));

    assert!(vibe_rs::completion_manager::is_open(&app));
}
