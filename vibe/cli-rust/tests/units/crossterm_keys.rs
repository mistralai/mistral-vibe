//! Crossterm modifier representations preserve editing intent.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::chat_input::Action;
use vibe_rs::keymap::action_for;

#[test]
fn shifted_delete_line_accepts_both_character_cases() {
    for character in ['k', 'K'] {
        let key = KeyEvent::new(
            KeyCode::Char(character),
            KeyModifiers::CONTROL | KeyModifiers::SHIFT,
        );
        assert_eq!(action_for(&key), Some(Action::DeleteLine));
    }
}

#[test]
fn system_shortcuts_do_not_insert_text() {
    for modifiers in [KeyModifiers::SUPER, KeyModifiers::HYPER, KeyModifiers::META] {
        let key = KeyEvent::new(KeyCode::Char('b'), modifiers);
        assert_eq!(action_for(&key), None);
    }
    let uppercase = KeyEvent::new(KeyCode::Char('B'), KeyModifiers::SHIFT);
    assert_eq!(action_for(&uppercase), Some(Action::Insert('B')));
}
