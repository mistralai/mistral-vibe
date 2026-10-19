//! A second Escape within `DOUBLE_ESC_DELAY` clears the input or enters rewind mode.

use std::time::{Duration, Instant};

use vibe_rs::rewind::{is_double_escape, DOUBLE_ESC_DELAY};

#[test]
fn an_unhurried_second_escape_still_counts() {
    let first = Instant::now();
    assert!(is_double_escape(
        Some(first),
        first + Duration::from_millis(350)
    ));
    assert!(is_double_escape(
        Some(first),
        first + DOUBLE_ESC_DELAY - Duration::from_millis(1)
    ));
}

#[test]
fn a_second_escape_past_the_window_rearms() {
    let first = Instant::now();
    assert!(!is_double_escape(Some(first), first + DOUBLE_ESC_DELAY));
    assert!(!is_double_escape(
        Some(first),
        first + Duration::from_secs(1)
    ));
}

#[test]
fn a_lone_escape_is_never_doubled() {
    assert!(!is_double_escape(None, Instant::now()));
}
