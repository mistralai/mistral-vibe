//! The list viewport follows the highlight and brings its non-selectable neighbours along.

use vibe_rs::ui::list_scroll::follow;

/// A list whose lines 0, 1 and 5 are headers; 7 is a trailing note.
fn is_option(line: usize) -> bool {
    !matches!(line, 0 | 1 | 5 | 7)
}

#[test]
fn moves_the_least_to_show_the_highlight() {
    assert_eq!(follow(0, 3, 10, 3..4, |_| true), 1);
    assert_eq!(follow(4, 3, 10, 2..3, |_| true), 2);
    assert_eq!(follow(1, 3, 10, 2..3, |_| true), 1, "already visible");
}

#[test]
fn the_first_option_brings_the_top_of_the_list() {
    assert_eq!(follow(4, 3, 8, 2..3, is_option), 0);
}

#[test]
fn an_option_brings_its_section_header() {
    assert_eq!(follow(6, 3, 12, 6..7, is_option), 5);
}

#[test]
fn the_last_option_brings_the_trailing_lines() {
    assert_eq!(follow(0, 3, 8, 6..7, is_option), 5);
}

#[test]
fn the_highlight_wins_when_its_context_does_not_fit() {
    assert_eq!(follow(4, 1, 8, 2..3, is_option), 2);
    assert_eq!(
        follow(0, 2, 8, 2..5, |_| true),
        2,
        "a tall option shows its top"
    );
}

#[test]
fn offsets_stay_in_range() {
    assert_eq!(follow(9, 3, 5, 0..0, |_| true), 2);
    assert_eq!(follow(9, 0, 5, 1..2, |_| true), 5);
    assert_eq!(
        follow(1, 3, 5, 7..8, |line| line < 5),
        1,
        "a stale highlight is ignored"
    );
}
