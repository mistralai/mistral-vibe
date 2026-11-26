//! Shared list moves: arrows wrap, selectable rows are skipped, digits name numbered rows.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::list_nav;

#[test]
fn arrows_wrap_at_both_ends() {
    assert_eq!(list_nav::wrap(2, 3, true), 0);
    assert_eq!(list_nav::wrap(0, 3, false), 2);
    assert_eq!(list_nav::wrap(1, 3, true), 2);
    assert_eq!(list_nav::wrap(5, 3, false), 1, "a stale index clamps first");
    assert_eq!(list_nav::wrap(0, 0, true), 0);
}

#[test]
fn wrapping_skips_rows_that_cannot_take_the_cursor() {
    let selectable = |index: usize| index != 0 && index != 3;
    assert_eq!(list_nav::wrap_selectable(4, 5, true, selectable), Some(1));
    assert_eq!(list_nav::wrap_selectable(1, 5, false, selectable), Some(4));
    assert_eq!(list_nav::wrap_selectable(1, 5, true, |_| false), None);
}

#[test]
fn a_plain_digit_names_a_numbered_row() {
    let key = |ch, modifiers| KeyEvent::new(KeyCode::Char(ch), modifiers);
    assert_eq!(list_nav::digit(&key('1', KeyModifiers::NONE), 3), Some(0));
    assert_eq!(list_nav::digit(&key('3', KeyModifiers::NONE), 3), Some(2));
    assert_eq!(list_nav::digit(&key('4', KeyModifiers::NONE), 3), None);
    assert_eq!(list_nav::digit(&key('0', KeyModifiers::NONE), 3), None);
    assert_eq!(list_nav::digit(&key('1', KeyModifiers::CONTROL), 3), None);
}
