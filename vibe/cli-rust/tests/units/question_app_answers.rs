//! What the question app records as an answer, when it submits, and the mount grace period.

use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::Instant;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::question_app::{other_option_idx, submit_option_idx};
use vibe_rs::question_input::handle_key;
use vibe_rs::server::{Client, QuestionChoice, UserQuestion};

fn question(multi_select: bool) -> UserQuestion {
    UserQuestion {
        question: "Pick".into(),
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

fn open(questions: Vec<UserQuestion>) -> App {
    let mut app = App::default();
    app.question_app.open = true;
    app.question_app.questions = questions;
    app
}

fn press(app: &mut App, code: KeyCode) {
    let client = Arc::new(Client::stub());
    handle_key(app, &client, KeyEvent::new(code, KeyModifiers::NONE));
}

fn submit_multi(app: &mut App, ticked: &[usize]) {
    app.question_app
        .multi_selections
        .insert(0, ticked.iter().copied().collect::<BTreeSet<_>>());
    app.question_app
        .list
        .select(submit_option_idx(app).unwrap());
    press(app, KeyCode::Enter);
}

#[test]
fn multi_select_joins_the_ticked_labels_in_option_order() {
    let mut app = open(vec![question(true)]);

    submit_multi(&mut app, &[1, 0]);

    assert_eq!(
        app.question_app.answers.get(&0),
        Some(&("Alpha, Beta".into(), false))
    );
    assert!(!app.question_app.open, "the last answer submits");
}

#[test]
fn multi_select_with_free_text_marks_the_answer_as_other() {
    let mut app = open(vec![question(true)]);
    app.question_app.other_texts.insert(0, "  custom  ".into());
    let other = other_option_idx(&app).unwrap();

    submit_multi(&mut app, &[0, other]);

    assert_eq!(
        app.question_app.answers.get(&0),
        Some(&("Alpha, custom".into(), true))
    );
}

#[test]
fn a_ticked_but_empty_free_text_row_is_left_out() {
    let mut app = open(vec![question(true)]);
    let other = other_option_idx(&app).unwrap();

    submit_multi(&mut app, &[0, other]);

    assert_eq!(
        app.question_app.answers.get(&0),
        Some(&("Alpha".into(), false))
    );
}

#[test]
fn submitting_a_multi_select_without_ticks_is_a_no_op() {
    let mut app = open(vec![question(true)]);

    submit_multi(&mut app, &[]);

    assert!(app.question_app.open);
    assert!(app.question_app.answers.is_empty());
}

#[test]
fn enter_on_a_multi_select_option_ticks_it_without_answering() {
    let mut app = open(vec![question(true)]);

    press(&mut app, KeyCode::Enter);

    assert_eq!(
        app.question_app.multi_selections.get(&0),
        Some(&BTreeSet::from([0]))
    );
    assert!(app.question_app.answers.is_empty());
    assert!(app.question_app.open);
}

#[test]
fn single_select_saves_an_option_at_once_and_submits_the_last_question() {
    let mut app = open(vec![question(false)]);

    press(&mut app, KeyCode::Enter);

    assert_eq!(
        app.question_app.answers.get(&0),
        Some(&("Alpha".into(), false))
    );
    assert!(!app.question_app.open);
}

#[test]
fn single_select_free_text_needs_text_to_answer() {
    let mut app = open(vec![question(false)]);
    app.question_app
        .list
        .select(other_option_idx(&app).unwrap());

    press(&mut app, KeyCode::Enter);
    assert!(app.question_app.answers.is_empty());
    assert!(app.question_app.open);

    for ch in "mine".chars() {
        press(&mut app, KeyCode::Char(ch));
    }
    press(&mut app, KeyCode::Enter);

    assert_eq!(
        app.question_app.answers.get(&0),
        Some(&("mine".into(), true))
    );
    assert!(!app.question_app.open);
}

#[test]
fn the_app_submits_only_once_every_question_is_answered() {
    let mut app = open(vec![question(false), question(false)]);

    press(&mut app, KeyCode::Enter);
    assert!(app.question_app.open);
    assert_eq!(app.question_app.current_question_idx, 1);

    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Enter);

    assert!(!app.question_app.open);
    assert_eq!(
        app.question_app.answers.get(&1),
        Some(&("Beta".into(), false))
    );
}

#[test]
fn enter_and_esc_are_ignored_within_the_grace_period_but_navigation_is_not() {
    let mut app = open(vec![question(false)]);
    app.question_app.mount_time = Some(Instant::now());

    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Enter);
    press(&mut app, KeyCode::Esc);

    assert_eq!(app.question_app.list.selected(), 1);
    assert!(app.question_app.open);
    assert!(app.question_app.answers.is_empty());

    app.question_app.mount_time = None;
    press(&mut app, KeyCode::Esc);
    assert!(!app.question_app.open);
    assert!(app.question_app.answers.is_empty());
}
