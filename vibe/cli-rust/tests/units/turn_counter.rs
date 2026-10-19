//! Tests for the client-side --max-turns counter.

use serde_json::json;
use vibe_rs::headless_run::TurnCounter;

#[test]
fn no_limit_never_stops() {
    let mut counter = TurnCounter::new(None);
    for _ in 0..10 {
        assert!(!counter.observe(&json!({"role": "assistant"})));
    }
}

#[test]
fn the_limit_is_exceeded_only_by_one_more_assistant_turn() {
    let mut counter = TurnCounter::new(Some(1));
    // The first assistant turn is within the limit.
    assert!(!counter.observe(&json!({"role": "assistant"})));
    // Tool entries do not count.
    assert!(!counter.observe(&json!({"role": "tool"})));
    assert!(!counter.observe(&json!({"type": "message"})));
    // The second assistant turn exceeds it.
    assert!(counter.observe(&json!({"role": "assistant"})));
}

#[test]
fn a_turn_within_the_limit_needs_no_stop() {
    let mut counter = TurnCounter::new(Some(3));
    for _ in 0..3 {
        assert!(!counter.observe(&json!({"role": "assistant"})));
    }
    assert!(counter.observe(&json!({"role": "assistant"})));
}
