//! Cmd+Left/Right and Home/End at a line edge move to the adjacent line.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::chat_input::{apply, selection_range, Action};
use vibe_rs::keymap::action_for;

fn mapped(code: KeyCode, mods: KeyModifiers) -> Action {
    action_for(&KeyEvent::new(code, mods)).expect("the key has an action")
}

fn state(input: &str, cursor: usize) -> (String, usize, Option<usize>) {
    (input.to_string(), cursor, None)
}

#[test]
fn line_start_and_end_climb_to_adjacent_lines_from_the_line_edges() {
    let (mut input, mut cursor, mut anchor) = state("one\n\n  three", 12);
    let mut step = |action: Action| {
        apply(&action, &mut input, &mut cursor, &mut anchor);
        cursor
    };
    assert_eq!(
        step(Action::CursorLineStart),
        7,
        "home first lands on the first word"
    );
    assert_eq!(step(Action::CursorLineStart), 5, "then on column 0");
    assert_eq!(
        step(Action::CursorLineStart),
        4,
        "then on the empty line above"
    );
    assert_eq!(
        step(Action::CursorLineStart),
        0,
        "then on the first line's start"
    );
    assert_eq!(step(Action::CursorLineEnd), 3, "end stops at the line end");
    assert_eq!(step(Action::CursorLineEnd), 4, "then the empty line below");
    assert_eq!(step(Action::CursorLineEnd), 12, "then the last line's end");
    assert_eq!(step(Action::CursorLineEnd), 12, "and stays at the text end");
}

#[test]
fn line_selection_extends_across_lines_from_the_line_edges() {
    let (mut input, mut cursor, mut anchor) = state("one\ntwo", 3);
    apply(&Action::SelectLineEnd, &mut input, &mut cursor, &mut anchor);
    assert_eq!(selection_range(&input, cursor, anchor), Some((3, 7)));
}

#[test]
fn command_arrows_move_to_the_line_edges() {
    let command = KeyModifiers::SUPER;
    let command_shift = KeyModifiers::SUPER | KeyModifiers::SHIFT;
    assert_eq!(mapped(KeyCode::Left, command), Action::CursorLineStart);
    assert_eq!(mapped(KeyCode::Right, command), Action::CursorLineEnd);
    assert_eq!(
        mapped(KeyCode::Left, command_shift),
        Action::SelectLineStart
    );
    assert_eq!(mapped(KeyCode::Right, command_shift), Action::SelectLineEnd);
}
