//! Pasting an image path again keeps it as literal text instead of an image placeholder.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::input::handle_paste;
use vibe_rs::long_paste::LITERAL_HINT;

use crate::support::{press, send_key, type_text};

const PATH: &str = "/tmp/vibe/shot.png";

fn notice(app: &App) -> Option<&str> {
    app.overlays
        .notice
        .as_ref()
        .map(|notice| notice.text.as_str())
}

#[test]
fn a_pasted_image_path_becomes_a_placeholder_with_a_hint() {
    let mut app = App::default();

    handle_paste(&mut app, PATH.into());

    assert_eq!(app.chat_input.input, "[Image #1] ");
    assert_eq!(notice(&app), Some(LITERAL_HINT));
}

#[test]
fn pasting_the_same_path_again_keeps_it_as_text() {
    let mut app = App::default();
    type_text(&mut app, "look at ");
    handle_paste(&mut app, PATH.into());

    handle_paste(&mut app, PATH.into());

    assert_eq!(app.chat_input.input, format!("look at {PATH}"));
    assert_eq!(app.chat_input.cursor, app.chat_input.input.len());
    assert!(notice(&app).is_none());
    assert!(app.chat_input.expandable_paste.is_none());
    assert_eq!(app.chat_input.submitted_text(), format!("look at {PATH}"));
}

#[test]
fn the_kept_path_is_plain_text_that_edits_never_turn_into_an_image() {
    let mut app = App::default();
    type_text(&mut app, "see ");
    handle_paste(&mut app, PATH.into());
    handle_paste(&mut app, PATH.into());
    assert!(app
        .chat_input
        .mentions
        .spans(&app.chat_input.input)
        .is_empty());

    app.chat_input.cursor = "see ".len();
    press(&mut app, KeyCode::Backspace);
    assert_eq!(app.chat_input.input, format!("see{PATH}"));
    press(&mut app, KeyCode::Char(' '));
    press(&mut app, KeyCode::End);
    type_text(&mut app, " please");

    assert_eq!(app.chat_input.input, format!("see {PATH} please"));
    assert_eq!(app.chat_input.cursor, app.chat_input.input.len());
}

#[test]
fn a_pasted_image_list_comes_back_as_pasted() {
    let mut app = App::default();
    let pasted = "/tmp/a.png /tmp/b.png";
    handle_paste(&mut app, pasted.into());
    assert_eq!(app.chat_input.input, "[Image #1] [Image #2] ");

    handle_paste(&mut app, pasted.into());

    assert_eq!(app.chat_input.input, pasted);
}

#[test]
fn undo_after_keeping_the_path_brings_the_placeholder_back() {
    let mut app = App::default();
    handle_paste(&mut app, PATH.into());
    handle_paste(&mut app, PATH.into());

    assert!(app.chat_input.restore_edit(false));

    assert_eq!(app.chat_input.input, "[Image #1] ");
}

#[test]
fn typing_ends_the_offer_so_the_path_is_a_new_image() {
    let mut app = App::default();
    handle_paste(&mut app, PATH.into());

    press(&mut app, KeyCode::Char('x'));
    assert!(notice(&app).is_none());
    handle_paste(&mut app, PATH.into());

    assert_eq!(app.chat_input.input, "[Image #1] x [Image #2] ");
}

#[test]
fn a_different_path_is_another_image() {
    let mut app = App::default();
    handle_paste(&mut app, PATH.into());

    handle_paste(&mut app, "/tmp/other.png".into());

    assert_eq!(app.chat_input.input, "[Image #1] [Image #2] ");
}

#[test]
fn undoing_the_paste_ends_the_offer() {
    let mut app = App::default();
    handle_paste(&mut app, PATH.into());

    send_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('z'), KeyModifiers::SUPER),
    );

    assert!(app.chat_input.input.is_empty());
    assert!(notice(&app).is_none());
    assert!(app.chat_input.expandable_paste.is_none());
}

#[test]
fn a_caret_before_the_placeholder_stays_there() {
    let mut app = App::default();
    type_text(&mut app, "see ");
    handle_paste(&mut app, PATH.into());
    press(&mut app, KeyCode::Home);

    handle_paste(&mut app, PATH.into());

    assert_eq!(app.chat_input.input, format!("see {PATH}"));
    assert_eq!(app.chat_input.cursor, 0);
}

#[test]
fn a_dropped_path_comes_back_exactly_as_dropped() {
    for (before, dropped) in [("see ", format!("{PATH} ")), ("see", format!("{PATH}\n"))] {
        let mut app = App::default();
        type_text(&mut app, before);
        handle_paste(&mut app, dropped.clone());

        handle_paste(&mut app, dropped.clone());

        assert_eq!(app.chat_input.input, format!("{before}{dropped}"));
        assert_eq!(app.chat_input.cursor, app.chat_input.input.len());
    }
}
