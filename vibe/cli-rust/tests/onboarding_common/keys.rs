//! The wizard's plain key events and typing, shared by the key-driving
//! onboarding binaries. Included per test binary via `#[path]`.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::setup::wizard::{screens, OnboardingState};

/// Enter.
pub(crate) fn enter() -> KeyEvent {
    KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)
}

/// Esc.
#[allow(dead_code)]
pub(crate) fn esc() -> KeyEvent {
    KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)
}

/// Tab.
pub(crate) fn tab() -> KeyEvent {
    KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE)
}

/// Down arrow.
pub(crate) fn down() -> KeyEvent {
    KeyEvent::new(KeyCode::Down, KeyModifiers::NONE)
}

/// Type `text` into the wizard one character per key event.
pub(crate) fn type_into(state: &mut OnboardingState, text: &str) {
    for character in text.chars() {
        screens::handle_key(
            state,
            KeyEvent::new(KeyCode::Char(character), KeyModifiers::NONE),
        );
    }
}
