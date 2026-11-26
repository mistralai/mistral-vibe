//! Up/Down recall history only from the first or last visual row of a wrapped or multiline draft.

use crossterm::event::KeyCode;
use vibe_rs::app::App;

use crate::support::press;

const PRESS_LIMIT: usize = 64;

struct Composer {
    app: App,
}

impl Composer {
    fn with_history(entries: &[&str]) -> Self {
        let mut app = App::default();
        app.view.input_area.width = 40;
        app.view.input_area.height = 12;
        for entry in entries {
            app.chat_input.history.add(entry);
        }
        Self { app }
    }

    fn draft(entries: &[&str], text: &str) -> Self {
        let mut composer = Self::with_history(entries);
        composer.app.chat_input.input = text.to_owned();
        composer.app.chat_input.cursor = text.len();
        composer
    }

    fn press(&mut self, code: KeyCode) {
        press(&mut self.app, code);
    }

    fn input(&self) -> &str {
        &self.app.chat_input.input
    }

    /// Press `code` until the text changes; returns the presses that only moved the caret.
    fn press_until_recall(&mut self, code: KeyCode) -> usize {
        let before = self.input().to_owned();
        for moves in 0..PRESS_LIMIT {
            self.press(code);
            if self.input() != before {
                return moves;
            }
        }
        panic!("{code:?} never recalled history");
    }
}

fn wrapped_multiline() -> String {
    format!("{}\nsecond line", "abcdefghij ".repeat(20))
}

#[test]
fn down_moves_to_the_next_line_after_a_manual_move_in_a_recalled_entry() {
    let mut composer = Composer::with_history(&["first line\nsecond line"]);
    composer.press(KeyCode::Up);
    assert_eq!(composer.input(), "first line\nsecond line");
    assert_eq!(composer.app.chat_input.cursor, "first line".len());

    composer.press(KeyCode::Left);
    composer.press(KeyCode::Down);

    assert_eq!(composer.input(), "first line\nsecond line");
    assert!(composer.app.chat_input.cursor > "first line\n".len());
}

#[test]
fn up_recalls_history_only_from_the_start_of_the_first_wrapped_row() {
    let mut composer = Composer::draft(&["hello", "how are you?"], &"abcdefghij ".repeat(20));

    let moves = composer.press_until_recall(KeyCode::Up);

    assert!(moves >= 2, "only {moves} caret moves before recall");
    assert_eq!(composer.input(), "how are you?");
}

#[test]
fn down_on_the_last_wrapped_row_leaves_history_alone() {
    let draft = format!("LONG {}", "x".repeat(160));
    let mut composer = Composer::draft(&["how are you?"], &draft);

    composer.press(KeyCode::Down);
    assert_eq!(composer.input(), draft);
    composer.app.chat_input.cursor = 0;
    composer.press(KeyCode::Up);

    assert_eq!(composer.input(), "how are you?");
}

#[test]
fn down_right_after_loading_a_wrapped_entry_recalls_the_next_one() {
    let entry = wrapped_multiline();
    let mut composer = Composer::with_history(&[&entry]);
    composer.press(KeyCode::Up);
    assert_eq!(composer.input(), entry);

    composer.press(KeyCode::Down);

    assert_eq!(composer.input(), "");
}

#[test]
fn up_right_after_loading_a_wrapped_entry_recalls_the_older_one() {
    let entry = wrapped_multiline();
    let mut composer = Composer::with_history(&["older message", &entry, "Hi there"]);
    composer.press(KeyCode::Up);
    composer.press(KeyCode::Up);
    assert_eq!(composer.input(), entry);

    composer.press(KeyCode::Up);

    assert_eq!(composer.input(), "older message");
}

#[test]
fn down_recalls_the_next_entry_from_the_last_row_after_a_manual_move() {
    let entry = wrapped_multiline();
    let mut composer = Composer::with_history(&["older message", &entry, "Hi there"]);
    composer.press(KeyCode::Up);
    composer.press(KeyCode::Up);
    composer.press(KeyCode::Left);

    let moves = composer.press_until_recall(KeyCode::Down);

    assert!(moves >= 1, "Down recalled before reaching the last row");
    assert_eq!(composer.input(), "Hi there");
}
