//! A digit picks the numbered question row it names.

use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::Instant;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::question_input::handle_key;
use vibe_rs::server::{Client, QuestionChoice, UserQuestion};

fn question(text: &str, multi_select: bool) -> UserQuestion {
    UserQuestion {
        question: text.into(),
        header: String::new(),
        options: ["Alpha", "Beta"]
            .into_iter()
            .map(|label| QuestionChoice {
                label: label.into(),
                description: String::new(),
            })
            .collect(),
        multi_select,
        hide_other: false,
    }
}

/// Two questions keep the app open after the first answer, so the state stays observable.
fn open(multi_select: bool) -> App {
    let mut app = App::default();
    app.question_app.open = true;
    app.question_app.questions = vec![question("First", multi_select), question("Second", false)];
    app
}

fn press(app: &mut App, ch: char) {
    let client = Arc::new(Client::stub());
    handle_key(
        app,
        &client,
        KeyEvent::new(KeyCode::Char(ch), KeyModifiers::NONE),
    );
}

#[test]
fn a_digit_answers_its_option_and_advances_to_the_next_question() {
    let mut app = open(false);

    press(&mut app, '2');

    assert_eq!(
        app.question_app.answers.get(&0),
        Some(&("Beta".into(), false))
    );
    assert_eq!(app.question_app.current_question_idx, 1);
    assert!(app.question_app.open);
}

#[test]
fn one_names_the_first_option() {
    let mut app = open(false);
    app.question_app.list.select(1);

    press(&mut app, '1');

    assert_eq!(
        app.question_app.answers.get(&0),
        Some(&("Alpha".into(), false))
    );
}

#[test]
fn the_free_text_digit_only_focuses_its_row() {
    let mut app = open(false);

    press(&mut app, '3');

    assert_eq!(app.question_app.list.selected(), 2);
    assert_eq!(app.question_app.current_question_idx, 0);
    assert!(app.question_app.answers.is_empty());
}

#[test]
fn the_free_text_digit_does_not_validate_typed_text() {
    let mut app = open(false);
    app.question_app.other_texts.insert(0, "custom".into());

    press(&mut app, '3');

    assert_eq!(app.question_app.list.selected(), 2);
    assert_eq!(app.question_app.other_cursor, "custom".len());
    assert!(app.question_app.answers.is_empty());
}

#[test]
fn zero_and_out_of_range_digits_are_ignored() {
    let mut app = open(false);

    press(&mut app, '0');
    press(&mut app, '4');
    press(&mut app, '9');

    assert_eq!(app.question_app.list.selected(), 0);
    assert_eq!(app.question_app.current_question_idx, 0);
    assert!(app.question_app.answers.is_empty());
}

#[test]
fn a_digit_ticks_and_unticks_a_multi_select_option() {
    let mut app = open(true);

    press(&mut app, '2');
    assert_eq!(
        app.question_app.multi_selections.get(&0),
        Some(&BTreeSet::from([1]))
    );
    assert_eq!(app.question_app.list.selected(), 1);

    press(&mut app, '2');
    assert_eq!(
        app.question_app.multi_selections.get(&0),
        Some(&BTreeSet::new())
    );
    assert!(app.question_app.answers.is_empty());
    assert_eq!(app.question_app.current_question_idx, 0);
}

#[test]
fn the_submit_row_has_no_digit() {
    let mut app = open(true);
    press(&mut app, '1');

    press(&mut app, '4');

    assert_eq!(app.question_app.list.selected(), 0);
    assert!(app.question_app.answers.is_empty());
    assert_eq!(app.question_app.current_question_idx, 0);
}

#[test]
fn a_digit_is_consumed_without_acting_within_the_grace_period() {
    let mut app = open(false);
    app.question_app.mount_time = Some(Instant::now());

    press(&mut app, '2');

    assert_eq!(app.question_app.list.selected(), 0);
    assert!(app.question_app.answers.is_empty());

    app.question_app.mount_time = None;
    press(&mut app, '2');
    assert_eq!(
        app.question_app.answers.get(&0),
        Some(&("Beta".into(), false))
    );
}
