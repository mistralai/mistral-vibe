//! Key-to-Action mapping, word/line editing mechanics, and paste filtering.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::chat_input::{apply, selection_range, Action};
use vibe_rs::keymap::action_for;

fn key(code: KeyCode, mods: KeyModifiers) -> KeyEvent {
    KeyEvent::new(code, mods)
}

fn mapped(code: KeyCode, mods: KeyModifiers) -> Action {
    action_for(&key(code, mods)).expect("the key has an action")
}

#[test]
fn plain_chars_and_unmapped_keys() {
    assert_eq!(
        mapped(KeyCode::Char('a'), KeyModifiers::NONE),
        Action::Insert('a')
    );
    assert_eq!(
        mapped(KeyCode::Char('Z'), KeyModifiers::NONE),
        Action::Insert('Z')
    );
    // Submit, history up/down, and scroll keys are handled outside the
    // chat-input action map.
    for code in [KeyCode::Enter, KeyCode::Up, KeyCode::Down, KeyCode::PageUp] {
        assert_eq!(
            action_for(&key(code, KeyModifiers::NONE)),
            None,
            "{code:?} must stay unmapped"
        );
    }
}

#[test]
fn ctrl_letters_map_to_line_edit_actions() {
    let ctrl = KeyModifiers::CONTROL;
    assert_eq!(mapped(KeyCode::Char('a'), ctrl), Action::CursorLineStart);
    assert_eq!(mapped(KeyCode::Char('e'), ctrl), Action::CursorLineEnd);
    assert_eq!(
        mapped(KeyCode::Char('u'), ctrl),
        Action::DeleteToStartOfLine
    );
    assert_eq!(mapped(KeyCode::Char('k'), ctrl), Action::DeleteToEndOfLine);
    assert_eq!(mapped(KeyCode::Char('w'), ctrl), Action::DeleteWordLeft);
    assert_eq!(mapped(KeyCode::Char('j'), ctrl), Action::Insert('\n'));
    // Unbound Ctrl/Alt combos are swallowed, never self-inserted.
    assert_eq!(action_for(&key(KeyCode::Char('x'), ctrl)), None);
    assert_eq!(
        action_for(&key(KeyCode::Char('x'), KeyModifiers::ALT)),
        None
    );
}

#[test]
fn alt_word_aliases_match_textual() {
    let alt = KeyModifiers::ALT;
    let ctrl = KeyModifiers::CONTROL;
    assert_eq!(mapped(KeyCode::Char('b'), alt), Action::CursorWordLeft);
    assert_eq!(mapped(KeyCode::Char('f'), alt), Action::CursorWordRight);
    assert_eq!(mapped(KeyCode::Backspace, alt), Action::DeleteWordLeft);
    assert_eq!(mapped(KeyCode::Delete, alt), Action::DeleteWordRight);
    assert_eq!(mapped(KeyCode::Left, ctrl), Action::CursorWordLeft);
    assert_eq!(mapped(KeyCode::Right, ctrl), Action::CursorWordRight);
    assert_eq!(mapped(KeyCode::Backspace, ctrl), Action::DeleteWordLeft);
}

#[test]
fn shift_extends_selections_and_f_keys_select() {
    let shift = KeyModifiers::SHIFT;
    let shift_ctrl = KeyModifiers::SHIFT | KeyModifiers::CONTROL;
    assert_eq!(mapped(KeyCode::Left, shift), Action::SelectLeft);
    assert_eq!(mapped(KeyCode::Right, shift), Action::SelectRight);
    assert_eq!(mapped(KeyCode::Left, shift_ctrl), Action::SelectWordLeft);
    assert_eq!(mapped(KeyCode::Right, shift_ctrl), Action::SelectWordRight);
    assert_eq!(mapped(KeyCode::Home, shift), Action::SelectLineStart);
    assert_eq!(mapped(KeyCode::End, shift), Action::SelectLineEnd);
    assert_eq!(mapped(KeyCode::F(7), KeyModifiers::NONE), Action::SelectAll);
    assert_eq!(
        mapped(KeyCode::F(6), KeyModifiers::NONE),
        Action::SelectLine
    );
    assert_eq!(
        mapped(KeyCode::Enter, shift),
        Action::Insert('\n'),
        "shift+enter inserts a newline"
    );
}

#[test]
fn home_and_delete_map_plain() {
    assert_eq!(
        mapped(KeyCode::Home, KeyModifiers::NONE),
        Action::CursorLineStart
    );
    assert_eq!(
        mapped(KeyCode::End, KeyModifiers::NONE),
        Action::CursorLineEnd
    );
    assert_eq!(
        mapped(KeyCode::Backspace, KeyModifiers::NONE),
        Action::DeleteLeft
    );
    assert_eq!(
        mapped(KeyCode::Delete, KeyModifiers::NONE),
        Action::DeleteRight
    );
}

fn state(input: &str, cursor: usize) -> (String, usize, Option<usize>) {
    (input.to_string(), cursor, None)
}

#[test]
fn word_motion_stops_at_word_boundaries() {
    let (mut input, mut cursor, mut anchor) = state("foo bar baz", 11);
    apply(
        &Action::CursorWordLeft,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(cursor, 8, "stops before the last word");
    apply(
        &Action::CursorWordLeft,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(cursor, 4, "stops at the word boundary");
    apply(
        &Action::CursorWordLeft,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(cursor, 0);
    apply(
        &Action::CursorWordRight,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(cursor, 3);
    apply(
        &Action::CursorWordRight,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(cursor, 7, "lands at the end of the current word");
    apply(
        &Action::CursorWordRight,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(cursor, 11);
    assert_eq!(input, "foo bar baz");
}

#[test]
fn word_deletes_eat_the_separator_too() {
    let (mut input, mut cursor, mut anchor) = state("foo bar baz", 11);
    apply(
        &Action::DeleteWordLeft,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!((input.as_str(), cursor), ("foo bar ", 8));
    apply(
        &Action::DeleteWordLeft,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!((input.as_str(), cursor), ("foo ", 4));

    let (mut input, mut cursor, mut anchor) = state("foo bar", 0);
    apply(
        &Action::DeleteWordRight,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!((input.as_str(), cursor), (" bar", 0));
}

#[test]
fn multiline_caret_crosses_line_boundaries() {
    let (mut input, mut cursor, mut anchor) = state("one\ntwo", 4);
    apply(
        &Action::CursorWordLeft,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(cursor, 3, "column 0 jumps to the previous line end");
    apply(
        &Action::CursorWordRight,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(cursor, 4, "line end jumps to the next line start");
}

#[test]
fn smart_home_toggles_to_first_non_whitespace() {
    let (mut input, mut cursor, mut anchor) = state("    indented", 12);
    apply(
        &Action::CursorLineStart,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(cursor, 4, "first home lands on the first word");
    apply(
        &Action::CursorLineStart,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(cursor, 0, "second home goes to column 0");
    apply(&Action::CursorLineEnd, &mut input, &mut cursor, &mut anchor);
    assert_eq!(cursor, 12);
}

#[test]
fn line_deletes_join_and_drop_lines() {
    // Ctrl+U at column 0 joins with the line above.
    let (mut input, mut cursor, mut anchor) = state("one\ntwo", 4);
    apply(
        &Action::DeleteToStartOfLine,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!((input.as_str(), cursor), ("onetwo", 3));

    // Ctrl+K at line end pulls the next line up.
    let (mut input, mut cursor, mut anchor) = state("one\ntwo", 3);
    apply(
        &Action::DeleteToEndOfLine,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!((input.as_str(), cursor), ("onetwo", 3));

    // Ctrl+K on an empty line drops it entirely.
    let (mut input, mut cursor, mut anchor) = state("one\n\ntwo", 4);
    apply(
        &Action::DeleteToEndOfLine,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!((input.as_str(), cursor), ("one\ntwo", 4));
}

#[test]
fn edits_replace_the_selection_first() {
    let mut input = "keep DROP keep".to_string();
    let mut cursor = 9;
    let mut anchor = Some(5);
    apply(&Action::Insert('x'), &mut input, &mut cursor, &mut anchor);
    assert_eq!((input.as_str(), cursor), ("keep x keep", 6));
    assert_eq!(anchor, None, "insert collapses the anchor");
}

#[test]
fn select_all_and_select_line_set_ordered_ranges() {
    let mut input = "one two".to_string();
    let mut cursor = 3;
    let mut anchor = None;
    apply(&Action::SelectAll, &mut input, &mut cursor, &mut anchor);
    assert_eq!(selection_range(&input, cursor, anchor), Some((0, 7)));

    let (mut input, mut cursor, mut anchor) = state("one\nlong line\ntwo", 8);
    apply(&Action::SelectLine, &mut input, &mut cursor, &mut anchor);
    assert_eq!(selection_range(&input, cursor, anchor), Some((4, 13)));
}

#[test]
fn unicode_chars_move_and_delete_as_whole_chars() {
    let mut input = "café".to_string();
    let mut cursor = 5;
    let mut anchor = None;
    apply(&Action::CursorLeft, &mut input, &mut cursor, &mut anchor);
    assert_eq!(cursor, 3, "one code point, not one byte");
    apply(&Action::DeleteLeft, &mut input, &mut cursor, &mut anchor);
    assert_eq!((input.as_str(), cursor), ("caé", 2));

    let (mut input, mut cursor, mut anchor) = state("héllo", 1);
    apply(&Action::DeleteRight, &mut input, &mut cursor, &mut anchor);
    assert_eq!(input, "hllo");
}

#[test]
fn selection_extends_by_word() {
    let mut input = "one two".to_string();
    let mut cursor = 7;
    let mut anchor = None;
    apply(
        &Action::SelectWordLeft,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(selection_range(&input, cursor, anchor), Some((4, 7)));
    apply(
        &Action::SelectWordLeft,
        &mut input,
        &mut cursor,
        &mut anchor,
    );
    assert_eq!(selection_range(&input, cursor, anchor), Some((0, 7)));
    apply(&Action::CursorLeft, &mut input, &mut cursor, &mut anchor);
    assert_eq!(anchor, None, "plain moves drop the selection");
}

#[test]
fn bracketed_paste_normalizes_carriage_returns() {
    let mut app = App::default();
    app.chat_input.input = "end".into();
    app.chat_input.cursor = 3;
    vibe_rs::input::handle_paste(&mut app, "a\r\nb\rc".into());
    assert_eq!(app.chat_input.input, "enda\nb\nc");
    assert_eq!(app.chat_input.cursor, app.chat_input.input.len());
    assert_eq!(app.chat_input.anchor, None);
}

#[test]
fn paste_replaces_the_active_selection() {
    let mut app = App::default();
    app.chat_input.input = "keep DROP keep".into();
    app.chat_input.cursor = 9;
    app.chat_input.anchor = Some(5);
    vibe_rs::input::handle_paste(&mut app, "PASTED".into());
    assert_eq!(app.chat_input.input, "keep PASTED keep");
    assert_eq!(app.chat_input.cursor, 11);
}
