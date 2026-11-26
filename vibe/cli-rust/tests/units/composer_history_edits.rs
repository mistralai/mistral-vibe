//! Undo history covers deferred image and voice edits without losing the previous draft.

use vibe_rs::app::App;
use vibe_rs::{input, paste_image, voice::VoiceEvent};

#[test]
fn image_paste_undo_restores_the_replaced_selection() {
    let mut app = App::default();
    input::handle_paste(&mut app, "selected draft".to_owned());
    app.chat_input.anchor = Some(0);
    let cursor = app.chat_input.cursor;

    paste_image::apply_event(
        &mut app,
        paste_image::Event::Pasted {
            path: "image.png".into(),
        },
    );
    let pasted = app.chat_input.input.clone();
    assert!(pasted.contains("[Image #1]"));
    assert!(app.chat_input.restore_edit(false));
    assert_eq!(app.chat_input.input, "selected draft");
    assert_eq!(app.chat_input.cursor, cursor);
    assert_eq!(app.chat_input.anchor, Some(0));
    assert!(app.chat_input.restore_edit(true));
    assert_eq!(app.chat_input.input, pasted);
    assert!(app.chat_input.restore_edit(false));
    assert!(app.chat_input.restore_edit(false));
    assert!(app.chat_input.input.is_empty());
}

#[test]
fn dictation_is_undoable_without_discarding_earlier_typing() {
    let mut app = App::default();
    input::handle_paste(&mut app, "draft ".to_owned());
    app.apply_voice_event(VoiceEvent::TextDelta("spoken text".to_owned()));

    assert_eq!(app.chat_input.input, "draft spoken text");
    assert!(app.chat_input.restore_edit(false));
    assert_eq!(app.chat_input.input, "draft ");
    assert!(app.chat_input.restore_edit(true));
    assert_eq!(app.chat_input.input, "draft spoken text");
    assert!(app.chat_input.restore_edit(false));
    assert!(app.chat_input.restore_edit(false));
    assert!(app.chat_input.input.is_empty());
}
