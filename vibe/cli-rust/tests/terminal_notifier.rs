//! Pure terminal title and notification transitions.

use std::time::{Duration, Instant};
use vibe_rs::program_status::{BlockedKind, ProgramState};
use vibe_rs::terminal_notifier::{updated_title, NotificationContext, TerminalNotifier};

#[test]
fn titles_are_normalized_and_control_sequences_cannot_escape() {
    let mut notifier = TerminalNotifier::default();
    let state = ProgramState::Idle;
    assert_eq!(notifier.take_title(state).as_deref(), Some("Vibe"));
    assert!(notifier.take_title(state).is_none());
    notifier.set_default_title("  A useful title  ");
    assert_eq!(notifier.title(state), "A useful title");
    notifier.set_default_title("A\x1b]2;bad\x07\n\r\t\u{7f}\u{85}\u{9f} title");
    assert_eq!(notifier.title(state), "A]2;bad title");
    notifier.set_default_title(" \t\n \u{1c}\u{1d}\u{1e}\u{1f}");
    assert_eq!(notifier.title(state), "Vibe");
}

#[test]
fn focus_acknowledges_suffix_but_preserves_waiting() {
    let mut notifier = TerminalNotifier::default();
    notifier.configure(true, true);
    let mut state = ProgramState::Working;
    notifier.clear_outcome();
    assert_eq!(notifier.title(state), ">> Vibe");
    notifier.set_focus(false);
    state = ProgramState::Blocked(BlockedKind::Permission);
    notifier.notify(NotificationContext::ActionRequired, Instant::now());
    assert_eq!(notifier.title(state), "Vibe - Action Required");
    assert!(notifier.take_bell());
    assert!(!notifier.take_bell());
    notifier.set_focus(true);
    assert_eq!(notifier.title(state), "? Vibe");
    notifier.set_focus(false);
    assert_eq!(notifier.title(state), "? Vibe");
    assert!(!notifier.take_bell());
    state = ProgramState::Working;
    notifier.clear_outcome();
    assert_eq!(notifier.title(state), ">> Vibe");
}

#[test]
fn completion_is_terminal_and_bells_are_throttled() {
    let now = Instant::now();
    let mut notifier = TerminalNotifier::default();
    notifier.configure(true, true);
    notifier.set_focus(false);
    notifier.notify(NotificationContext::ActionRequired, now);
    assert!(notifier.take_bell());
    notifier.notify(
        NotificationContext::Complete,
        now + Duration::from_millis(500),
    );
    let state = ProgramState::Done;
    assert!(!notifier.take_bell());
    assert_eq!(notifier.title(state), "Vibe - Task Complete");
    notifier.set_default_title("Renamed");
    assert_eq!(notifier.title(state), "Renamed - Task Complete");
    notifier.set_focus(true);
    assert_eq!(notifier.title(state), "Renamed");
    notifier.set_focus(false);
    notifier.notify(NotificationContext::Complete, now + Duration::from_secs(1));
    assert!(notifier.take_bell());
}

#[test]
fn invalidating_title_preserves_notification_without_repeating_bell() {
    let mut notifier = TerminalNotifier::default();
    notifier.configure(true, true);
    notifier.set_focus(false);
    let state = ProgramState::Blocked(BlockedKind::Permission);
    notifier.notify(NotificationContext::ActionRequired, Instant::now());
    assert!(notifier.take_bell());
    assert_eq!(
        notifier.take_title(state).as_deref(),
        Some("Vibe - Action Required")
    );
    assert!(notifier.take_title(state).is_none());
    notifier.invalidate_title();
    assert_eq!(
        notifier.take_title(state).as_deref(),
        Some("Vibe - Action Required")
    );
    assert!(!notifier.take_bell());
    assert!(notifier.take_title(state).is_none());
}

#[test]
fn config_gates_indicators_and_notifications_independently() {
    let mut notifier = TerminalNotifier::default();
    notifier.configure(true, false);
    let mut state = ProgramState::Working;
    notifier.clear_outcome();
    assert_eq!(notifier.title(state), "Vibe");
    notifier.set_focus(false);
    notifier.notify(NotificationContext::Complete, Instant::now());
    assert_eq!(notifier.title(state), "Vibe - Task Complete");
    assert!(notifier.take_bell());
    notifier.set_focus(true);
    notifier.configure(false, true);
    notifier.set_focus(false);
    state = ProgramState::Blocked(BlockedKind::Permission);
    notifier.notify(NotificationContext::ActionRequired, Instant::now());
    assert_eq!(notifier.title(state), "? Vibe");
    assert!(!notifier.take_bell());
    notifier.configure(false, false);
    assert_eq!(notifier.title(state), "Vibe");
}

#[test]
fn only_the_final_title_patch_changes_the_title() {
    use serde_json::json;
    assert_eq!(
        updated_title(&json!({"patch": [
            {"op": "replace", "path": "/title", "value": "first"},
            {"op": "add", "path": "/title", "value": "second"},
            {"op": "replace", "path": "/updatedAt", "value": 42}
        ]})),
        Some("second")
    );
    for last in [
        json!({"op":"remove", "path":"/title"}),
        json!({"op":"replace", "path":"/title", "value":null}),
    ] {
        assert_eq!(
            updated_title(&json!({"patch": [
                {"op": "replace", "path": "/title", "value": "first"}, last
            ]})),
            None
        );
    }
    assert_eq!(updated_title(&json!({"patch": []})), None);
}
