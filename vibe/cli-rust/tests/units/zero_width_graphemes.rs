//! Zero-width graphemes (e.g. U+180E) join a visible neighbour instead of taking their own cell.

use vibe_rs::chat_input::{apply, Action};
use vibe_rs::ui::tab_cells::cells;

const VOWEL_SEPARATOR: &str = "ᠷ\u{180E}ᠠ";

fn symbols(text: &str) -> Vec<&str> {
    cells(text, 0).map(|cell| cell.symbol).collect()
}

fn run(text: &str, cursor: usize, actions: &[Action]) -> (String, usize) {
    let (mut input, mut cursor, mut anchor) = (text.to_owned(), cursor, None);
    for action in actions {
        apply(action, &mut input, &mut cursor, &mut anchor);
    }
    (input, cursor)
}

#[test]
fn zero_width_grapheme_shares_a_cell_with_its_neighbour() {
    assert_eq!(symbols(VOWEL_SEPARATOR), ["ᠷ\u{180E}", "ᠠ"]);
    assert_eq!(symbols("\u{180E}ᠠ"), ["\u{180E}ᠠ"]);
    assert_eq!(symbols("a\u{200B}\tb"), ["a\u{200B}", "\t", "b"]);
}

#[test]
fn caret_never_stops_on_a_zero_width_grapheme() {
    let end = VOWEL_SEPARATOR.len();
    let left = [Action::CursorLeft, Action::CursorLeft];
    assert_eq!(run(VOWEL_SEPARATOR, end, &left).1, 0);
    assert_eq!(run(VOWEL_SEPARATOR, 0, &[Action::CursorRight]).1, 6);
    assert_eq!(run("a\n\u{180E}b", 7, &[Action::CursorLeft]).1, 2);
    assert_eq!(run("\u{180E}\nb", 0, &[Action::CursorRight]).1, 3);
}

#[test]
fn delete_removes_the_zero_width_grapheme_with_its_neighbour() {
    let end = VOWEL_SEPARATOR.len();
    assert_eq!(
        run(
            VOWEL_SEPARATOR,
            end,
            &[Action::CursorLeft, Action::DeleteLeft]
        ),
        ("ᠠ".to_owned(), 0)
    );
    assert_eq!(
        run(VOWEL_SEPARATOR, 0, &[Action::DeleteRight]),
        ("ᠠ".to_owned(), 0)
    );
}

#[test]
fn right_and_delete_from_inside_a_grapheme_stop_at_its_end() {
    let keycap = "1\u{FE0F}\u{20E3} x";
    assert_eq!(run(keycap, 1, &[Action::CursorRight]).1, 7);
    assert_eq!(
        run(keycap, 1, &[Action::DeleteRight]),
        ("1 x".to_owned(), 1)
    );
}

#[test]
fn zero_width_grapheme_after_a_tab_joins_the_tab() {
    let drawn: Vec<_> = cells("a\t\u{200B}", 0)
        .map(|cell| (cell.symbol, cell.width, cell.shown()))
        .collect();
    assert_eq!(drawn, [("a", 1, "a"), ("\t\u{200B}", 3, "   ")]);
}
