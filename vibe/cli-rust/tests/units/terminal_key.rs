//! The terminal key: `VIBE_TERMINAL_KEY` overrides detection, empty means no terminal.

use vibe_rs::terminal_key::resolve;

fn detected() -> Option<String> {
    Some("/dev/ttys003".into())
}

#[test]
fn without_override_the_detected_terminal_is_used() {
    assert_eq!(resolve(None, detected), Some("/dev/ttys003".into()));
    assert_eq!(resolve(None, || None), None);
}

#[test]
fn an_override_replaces_the_detected_terminal() {
    assert_eq!(
        resolve(Some("pane-1".into()), detected),
        Some("pane-1".into())
    );
}

#[test]
fn an_empty_override_means_no_terminal() {
    assert_eq!(resolve(Some(String::new()), detected), None);
}
