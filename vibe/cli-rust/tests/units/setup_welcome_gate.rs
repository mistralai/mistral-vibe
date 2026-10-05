//! `--setup`'s welcome gate: the overlapped boot still seeds before any
//! choice — the Enter that would leave the welcome screen is held until
//! the boot lands. The landing itself is inseparable from the async
//! select (verified by the PTY/e2e behavior instead), like the paint hold.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use vibe_rs::setup::wizard::{holds_welcome_enter, OnboardingState, Screen};

fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

fn welcome(done: bool) -> OnboardingState {
    OnboardingState {
        screen: Screen::Welcome,
        welcome_done: done,
        ..OnboardingState::default()
    }
}

#[test]
fn a_pending_boot_holds_the_enter_that_would_advance() {
    assert!(holds_welcome_enter(
        true,
        &welcome(true),
        &key(KeyCode::Enter)
    ));
}

#[test]
fn a_landed_boot_lets_the_welcome_enter_through() {
    assert!(!holds_welcome_enter(
        false,
        &welcome(true),
        &key(KeyCode::Enter)
    ));
}

#[test]
fn everything_else_stays_live_while_pending() {
    // The typing animation keeps running: an Enter before the text is
    // fully typed is inert with or without the gate.
    assert!(!holds_welcome_enter(
        true,
        &welcome(false),
        &key(KeyCode::Enter)
    ));
    let mut past_welcome = welcome(true);
    past_welcome.screen = Screen::ThemeSelection;
    assert!(!holds_welcome_enter(
        true,
        &past_welcome,
        &key(KeyCode::Enter)
    ));
    // Cancel stays available: Esc is never held.
    assert!(!holds_welcome_enter(
        true,
        &welcome(true),
        &key(KeyCode::Esc)
    ));
}
