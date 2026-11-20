//! Emacs Ctrl+P / Ctrl+N move between composer lines and recall history like Up / Down.

use vibe_rs::app::App;
use vibe_rs::vim::Mode;

use crate::support::{ctrl, send_key};

fn composer(entries: &[&str], text: &str) -> App {
    let mut app = App::default();
    app.view.input_area.width = 40;
    app.view.input_area.height = 12;
    for entry in entries {
        app.chat_input.history.add(entry);
    }
    app.chat_input.input = text.to_owned();
    app.chat_input.cursor = text.len();
    app
}

#[test]
fn ctrl_p_and_ctrl_n_move_between_lines() {
    let mut app = composer(&[], "first\nsecond");

    send_key(&mut app, ctrl('p'));
    assert_eq!(app.chat_input.cursor, "first".len());

    send_key(&mut app, ctrl('n'));
    assert_eq!(
        app.chat_input.cursor,
        "first\nfirst".len(),
        "the caret keeps its column"
    );
    assert!(!app.rail.focused, "Ctrl+N no longer reaches the rail");
}

#[test]
fn ctrl_p_and_ctrl_n_recall_history_at_the_edges() {
    let mut app = composer(&["older", "newer"], "");

    send_key(&mut app, ctrl('p'));
    assert_eq!(app.chat_input.input, "newer");
    send_key(&mut app, ctrl('p'));
    assert_eq!(app.chat_input.input, "older");

    send_key(&mut app, ctrl('n'));
    assert_eq!(app.chat_input.input, "newer");
}

#[test]
fn emacs_caret_keys_also_move_in_vim_normal_mode() {
    let mut app = crate::vim::app(Mode::Normal, "abcd\nefgh", 1);
    app.view.input_area.width = 40;
    app.view.input_area.height = 12;

    send_key(&mut app, ctrl('f'));
    assert_eq!(app.chat_input.cursor, 2);
    send_key(&mut app, ctrl('n'));
    assert_eq!(app.chat_input.cursor, "abcd\nef".len());
    send_key(&mut app, ctrl('b'));
    assert_eq!(app.chat_input.cursor, "abcd\ne".len());
    send_key(&mut app, ctrl('p'));
    assert_eq!(app.chat_input.cursor, 1);
    assert_eq!(app.vim.mode, Mode::Normal);
}
