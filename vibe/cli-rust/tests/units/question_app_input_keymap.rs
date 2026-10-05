//! Composer keymap behavior on the question app's focused free-text row.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::question_app::other_option_idx;
use vibe_rs::question_input::handle_key;
use vibe_rs::server::{Client, QuestionChoice, UserQuestion};

fn question(question: &str, multi_select: bool) -> App {
    let mut app = App::default();
    app.question_app.questions.push(UserQuestion {
        question: question.into(),
        header: String::new(),
        options: vec![
            QuestionChoice {
                label: "First".into(),
                description: String::new(),
            },
            QuestionChoice {
                label: "Second".into(),
                description: String::new(),
            },
        ],
        multi_select,
        hide_other: false,
    });
    app
}

fn focused_other(mut app: App) -> App {
    app.question_app.selected_option = other_option_idx(&app).unwrap();
    app
}

struct Harness {
    app: App,
    client: Arc<Client>,
}

impl Harness {
    fn new(app: App) -> Self {
        Self {
            app,
            client: Arc::new(Client::stub()),
        }
    }

    fn press(&mut self, code: KeyCode, modifiers: KeyModifiers) {
        handle_key(&mut self.app, &self.client, KeyEvent::new(code, modifiers));
    }

    fn press_ctrl(&mut self, ch: char) {
        self.press(KeyCode::Char(ch), KeyModifiers::CONTROL);
    }

    fn type_text(&mut self, text: &str) {
        for ch in text.chars() {
            self.press(KeyCode::Char(ch), KeyModifiers::NONE);
        }
    }

    fn other(&self) -> &str {
        self.app
            .question_app
            .other_texts
            .get(&0)
            .map_or("", |text| text.as_str())
    }

    fn cursor(&self) -> usize {
        self.app.question_app.other_cursor
    }
}

#[test]
fn ctrl_a_and_ctrl_e_move_the_caret_to_the_line_ends() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));
    harness.type_text("hello world");

    harness.press_ctrl('a');
    assert_eq!(harness.cursor(), 0);

    harness.press_ctrl('e');
    assert_eq!(harness.cursor(), "hello world".len());
    assert_eq!(harness.other(), "hello world");
}

#[test]
fn ctrl_u_deletes_to_the_start_of_the_line() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));
    harness.type_text("hello world");

    harness.press_ctrl('u');

    assert_eq!(harness.other(), "");
    assert_eq!(harness.cursor(), 0);
}

#[test]
fn ctrl_k_deletes_to_the_end_of_the_line() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));
    harness.type_text("hello world");
    harness.press_ctrl('a');

    harness.press_ctrl('k');

    assert_eq!(harness.other(), "");
    assert_eq!(harness.cursor(), 0);
}

#[test]
fn ctrl_w_deletes_the_word_left_of_the_caret() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));
    harness.type_text("hello world");

    harness.press_ctrl('w');

    assert_eq!(harness.other(), "hello ");
    assert_eq!(harness.cursor(), "hello ".len());
}

#[test]
fn home_end_and_delete_behave_like_the_composer() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));
    harness.type_text("hello");

    harness.press(KeyCode::Home, KeyModifiers::NONE);
    assert_eq!(harness.cursor(), 0);

    harness.press(KeyCode::Delete, KeyModifiers::NONE);
    assert_eq!(harness.other(), "ello");
    assert_eq!(harness.cursor(), 0);

    harness.press(KeyCode::End, KeyModifiers::NONE);
    assert_eq!(harness.cursor(), "ello".len());
}

#[test]
fn ctrl_delete_deletes_the_word_right_of_the_caret() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));
    harness.type_text("hello world");
    harness.press_ctrl('a');

    harness.press(KeyCode::Delete, KeyModifiers::CONTROL);

    assert_eq!(harness.other(), " world");
    assert_eq!(harness.cursor(), 0);
}

#[test]
fn alt_b_and_alt_f_move_the_caret_by_words() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));
    harness.type_text("hello world");

    harness.press(KeyCode::Char('b'), KeyModifiers::ALT);
    assert_eq!(harness.cursor(), 6);

    harness.press(KeyCode::Char('f'), KeyModifiers::ALT);
    assert_eq!(harness.cursor(), "hello world".len());
}

#[test]
fn typing_and_backspace_retick_the_free_text_row_in_multi_select() {
    let other_idx = other_option_idx(&question("Pick", true)).unwrap();
    let mut harness = Harness::new(focused_other(question("Pick", true)));

    harness.type_text("x");
    assert!(harness.app.question_app.multi_selections[&0].contains(&other_idx));

    harness.press(KeyCode::Backspace, KeyModifiers::NONE);
    assert!(!harness.app.question_app.multi_selections[&0].contains(&other_idx));
}

#[test]
fn enter_on_the_focused_row_still_submits() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));
    harness.app.question_app.open = true;
    harness.type_text("draft");

    harness.press(KeyCode::Enter, KeyModifiers::NONE);

    assert_eq!(
        harness.app.question_app.answers.get(&0),
        Some(&("draft".into(), true))
    );
    assert!(!harness.app.question_app.open);
}

#[test]
fn up_and_down_still_move_rows_while_the_row_is_focused() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));

    harness.press(KeyCode::Up, KeyModifiers::NONE);
    assert_eq!(harness.app.question_app.selected_option, 1);

    harness.press(KeyCode::Down, KeyModifiers::NONE);
    assert_eq!(
        harness.app.question_app.selected_option,
        other_option_idx(&harness.app).unwrap()
    );
}

#[test]
fn esc_still_cancels_while_the_row_is_focused() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));
    harness.app.question_app.open = true;

    harness.press(KeyCode::Esc, KeyModifiers::NONE);

    assert!(!harness.app.question_app.open);
}

#[test]
fn number_keys_do_not_jump_rows_while_the_row_is_focused() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));

    harness.press(KeyCode::Char('1'), KeyModifiers::NONE);

    assert_eq!(
        harness.app.question_app.selected_option,
        other_option_idx(&harness.app).unwrap()
    );
    assert_eq!(harness.other(), "1");
}

#[test]
fn ctrl_j_and_shift_enter_do_not_insert_newlines() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));

    harness.press_ctrl('j');
    harness.press(KeyCode::Enter, KeyModifiers::SHIFT);

    assert_eq!(harness.other(), "");
}

#[test]
fn modified_enters_do_not_submit_the_focused_row() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));
    harness.app.question_app.open = true;
    harness.type_text("draft");

    harness.press(KeyCode::Enter, KeyModifiers::CONTROL);
    harness.press(KeyCode::Enter, KeyModifiers::ALT);

    assert_eq!(harness.other(), "draft");
    assert!(harness.app.question_app.answers.is_empty());
    assert!(harness.app.question_app.open);
}

#[test]
fn enter_and_shift_enter_still_select_on_an_option_row() {
    let mut harness = Harness::new(question("Choose", false));
    let mut second = Harness::new(question("Choose", false));
    second.app.question_app.selected_option = 1;

    harness.press(KeyCode::Enter, KeyModifiers::NONE);
    second.press(KeyCode::Enter, KeyModifiers::SHIFT);

    assert_eq!(
        harness.app.question_app.answers.get(&0),
        Some(&("First".into(), false))
    );
    assert_eq!(
        second.app.question_app.answers.get(&0),
        Some(&("Second".into(), false))
    );
}

#[test]
fn enter_on_the_focused_row_toggles_it_in_multi_select() {
    let other_idx = other_option_idx(&question("Pick", true)).unwrap();
    let mut harness = Harness::new(focused_other(question("Pick", true)));
    harness.app.question_app.open = true;

    harness.press(KeyCode::Enter, KeyModifiers::NONE);
    assert!(harness.app.question_app.multi_selections[&0].contains(&other_idx));

    harness.press(KeyCode::Enter, KeyModifiers::NONE);
    assert!(!harness.app.question_app.multi_selections[&0].contains(&other_idx));

    assert!(harness.app.question_app.open);
}

#[test]
fn a_no_op_edit_keeps_the_tick_on_an_empty_ticked_row() {
    let other_idx = other_option_idx(&question("Pick", true)).unwrap();
    let mut harness = Harness::new(focused_other(question("Pick", true)));
    harness
        .app
        .question_app
        .multi_selections
        .entry(0)
        .or_default()
        .insert(other_idx);

    harness.press(KeyCode::Backspace, KeyModifiers::NONE);

    assert!(harness.app.question_app.multi_selections[&0].contains(&other_idx));
}

#[test]
fn caret_moves_do_not_create_an_entry_for_untyped_text() {
    let mut harness = Harness::new(focused_other(question("Choose", false)));

    harness.press(KeyCode::Left, KeyModifiers::NONE);
    harness.press(KeyCode::Home, KeyModifiers::NONE);

    assert!(!harness.app.question_app.other_texts.contains_key(&0));
}
