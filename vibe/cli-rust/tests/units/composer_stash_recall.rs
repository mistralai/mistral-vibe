//! Ctrl+S on a recalled history entry stashes the typed draft Down would give back, as Ctrl+R does.

use crossterm::event::KeyCode;
use vibe_rs::app::App;

use crate::support::{ctrl, press, send_key, type_text};

/// `draft` typed, then the previous prompt recalled over it by Up.
fn recalled(app: &mut App, draft: &str) {
    app.chat_input.history.add("earlier prompt");
    type_text(app, draft);
    press(app, KeyCode::Home);
    press(app, KeyCode::Up);
    assert_eq!(app.chat_input.input, "earlier prompt");
}

#[test]
fn ctrl_s_on_a_recalled_entry_without_a_typed_draft_stashes_the_entry() {
    let mut app = App::default();
    recalled(&mut app, "");

    send_key(&mut app, ctrl('s'));
    assert_eq!(app.chat_input.input, "");

    send_key(&mut app, ctrl('s'));
    assert_eq!(app.chat_input.input, "earlier prompt");
}

#[test]
fn ctrl_s_on_a_recalled_entry_over_a_bare_mode_stashes_the_mode() {
    let mut app = App::default();
    recalled(&mut app, "!");

    send_key(&mut app, ctrl('s'));
    assert_eq!(app.chat_input.full_text(), "");

    send_key(&mut app, ctrl('s'));
    assert_eq!(app.chat_input.full_text(), "!");
}

#[test]
fn ctrl_s_on_a_recalled_entry_swaps_the_typed_draft_with_the_stash() {
    let mut app = App::default();
    type_text(&mut app, "stashed first");
    send_key(&mut app, ctrl('s'));
    recalled(&mut app, "typed draft");

    send_key(&mut app, ctrl('s'));
    assert_eq!(app.chat_input.input, "stashed first");

    send_key(&mut app, ctrl('s'));
    assert_eq!(app.chat_input.input, "typed draft");
}
