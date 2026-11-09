//! Ctrl+Y yanks the last killed text when nothing is selected, and copies otherwise.
#![cfg(unix)]

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::{App, Selection};
use vibe_rs::input::handle_paste;
use vibe_rs::long_paste::placeholder;
use vibe_rs::selection::{RegionId, ScrollTarget};

use crate::support::{press, send_key, type_text};

fn ctrl(app: &mut App, c: char) {
    send_key(app, KeyEvent::new(KeyCode::Char(c), KeyModifiers::CONTROL));
}

fn typed(text: &str) -> App {
    let mut app = App::default();
    type_text(&mut app, text);
    app
}

#[test]
fn ctrl_y_yanks_what_ctrl_k_killed() {
    let mut app = typed("hello world");
    ctrl(&mut app, 'a');
    for _ in 0..6 {
        press(&mut app, KeyCode::Right);
    }
    ctrl(&mut app, 'k');
    assert_eq!(app.chat_input.input, "hello ");

    ctrl(&mut app, 'a');
    ctrl(&mut app, 'y');

    assert_eq!(app.chat_input.input, "worldhello ");
    assert_eq!(app.chat_input.cursor, "world".len());
}

#[test]
fn the_last_kill_replaces_the_buffer() {
    let mut app = typed("one two");
    ctrl(&mut app, 'w');
    ctrl(&mut app, 'w');
    assert_eq!(app.chat_input.input, "");

    ctrl(&mut app, 'y');

    assert_eq!(app.chat_input.input, "one ");
}

#[test]
fn ctrl_y_with_an_empty_kill_buffer_does_nothing() {
    let mut app = typed("hello");

    ctrl(&mut app, 'y');

    assert_eq!(app.chat_input.input, "hello");
}

#[test]
fn ctrl_y_copies_a_selection_instead_of_yanking() {
    let mut app = typed("hello world");
    ctrl(&mut app, 'w');
    press(&mut app, KeyCode::F(7));

    ctrl(&mut app, 'y');

    assert_eq!(app.chat_input.input, "hello ");
    assert_eq!(app.chat_input.anchor, Some(0));
}

#[test]
fn a_yank_is_one_undo_step() {
    let mut app = typed("hello world");
    ctrl(&mut app, 'w');
    ctrl(&mut app, 'y');
    assert_eq!(app.chat_input.input, "hello world");

    send_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('z'), KeyModifiers::SUPER),
    );

    assert_eq!(app.chat_input.input, "hello ");
}

#[test]
fn a_mouse_selection_not_painted_yet_keeps_ctrl_y_from_yanking() {
    let mut app = typed("draft gone");
    ctrl(&mut app, 'w');
    app.selection.region = Some(Selection {
        owner: RegionId::Main,
        anchor: (0, 0),
        head: (5, 0),
        pending_copy: true,
        edge_scroll: 0,
        scroll_target: ScrollTarget::Transcript,
        table_cell: None,
        text: String::new(),
    });

    ctrl(&mut app, 'y');

    assert_eq!(app.chat_input.input, "draft ");
}

#[test]
fn a_yanked_image_placeholder_stays_attached() {
    let mut app = App::default();
    handle_paste(&mut app, "/tmp/a.png".to_owned());
    assert_eq!(app.chat_input.input, "[Image #1] ");
    ctrl(&mut app, 'w');
    ctrl(&mut app, 'y');
    assert_eq!(app.chat_input.input, "[Image #1] ");

    press(&mut app, KeyCode::Backspace);
    press(&mut app, KeyCode::Backspace);

    assert_eq!(app.chat_input.input, "");
}

#[test]
fn yanking_a_long_paste_twice_inserts_two_collapsed_copies() {
    let mut app = App::default();
    let paste = "x".repeat(1200);
    handle_paste(&mut app, paste.clone());
    ctrl(&mut app, 'w');

    ctrl(&mut app, 'y');
    ctrl(&mut app, 'y');

    assert_eq!(app.chat_input.input, placeholder(&paste).repeat(2));
    assert!(app.overlays.notice.is_none());
}

#[test]
fn a_yank_collapses_only_the_pastes_it_holds() {
    let mut app = App::default();
    handle_paste(&mut app, "/tmp/a.png".to_owned());
    type_text(&mut app, "intro ");
    let paste = "x".repeat(1200);
    handle_paste(&mut app, paste.clone());
    type_text(&mut app, " outro");
    let draft = format!("[Image #1] intro {} outro", placeholder(&paste));
    assert_eq!(app.chat_input.input, draft);
    ctrl(&mut app, 'u');

    ctrl(&mut app, 'y');

    assert_eq!(app.chat_input.input, draft);
    assert_eq!(app.chat_input.cursor, draft.len());
    assert_eq!(
        app.chat_input.submitted_text(),
        format!("[Image #1] intro {paste} outro")
    );
}

#[test]
fn long_typed_text_yanks_back_in_full() {
    let text = "y".repeat(1100);
    let mut app = typed(&text);
    ctrl(&mut app, 'u');

    ctrl(&mut app, 'y');

    assert_eq!(app.chat_input.input, text);
}
