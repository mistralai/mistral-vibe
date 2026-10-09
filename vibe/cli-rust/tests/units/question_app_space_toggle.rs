//! Space ticks the focused multi-select checkbox and never answers the question.

use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::Instant;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::question_app::{other_option_idx, submit_option_idx};
use vibe_rs::question_input::handle_key;
use vibe_rs::server::{Client, QuestionChoice, UserQuestion};

fn open_question(multi_select: bool) -> App {
    let mut app = App::default();
    app.question_app.open = true;
    app.question_app.questions.push(UserQuestion {
        question: "Pick".into(),
        header: String::new(),
        options: vec![
            QuestionChoice {
                label: "Alpha".into(),
                description: String::new(),
            },
            QuestionChoice {
                label: "Beta".into(),
                description: String::new(),
            },
        ],
        multi_select,
        hide_other: false,
    });
    app
}

fn press(app: &mut App, code: KeyCode) {
    let client = Arc::new(Client::stub());
    handle_key(app, &client, KeyEvent::new(code, KeyModifiers::NONE));
}

fn ticked(app: &App) -> BTreeSet<usize> {
    app.question_app
        .multi_selections
        .get(&0)
        .cloned()
        .unwrap_or_default()
}

#[test]
fn space_ticks_and_unticks_the_focused_option() {
    let mut app = open_question(true);

    press(&mut app, KeyCode::Char(' '));
    assert_eq!(ticked(&app), BTreeSet::from([0]));

    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Char(' '));
    assert_eq!(ticked(&app), BTreeSet::from([0, 1]));

    press(&mut app, KeyCode::Char(' '));
    assert_eq!(ticked(&app), BTreeSet::from([0]));
    assert!(app.question_app.open);
    assert!(app.question_app.answers.is_empty());
}

#[test]
fn space_on_the_submit_row_does_not_submit() {
    let mut app = open_question(true);
    press(&mut app, KeyCode::Char(' '));
    app.question_app.selected_option = submit_option_idx(&app).unwrap();

    press(&mut app, KeyCode::Char(' '));

    assert!(app.question_app.open);
    assert!(app.question_app.answers.is_empty());
    assert_eq!(ticked(&app), BTreeSet::from([0]));
}

#[test]
fn space_types_into_the_focused_free_text_row() {
    let mut app = open_question(true);
    app.question_app.selected_option = other_option_idx(&app).unwrap();

    for ch in "a b".chars() {
        press(&mut app, KeyCode::Char(ch));
    }

    assert_eq!(app.question_app.other_texts.get(&0).unwrap(), "a b");
    assert_eq!(
        ticked(&app),
        BTreeSet::from([other_option_idx(&app).unwrap()])
    );
}

#[test]
fn space_is_ignored_in_single_select() {
    let mut app = open_question(false);

    press(&mut app, KeyCode::Char(' '));

    assert!(app.question_app.open);
    assert!(app.question_app.answers.is_empty());
    assert!(app.question_app.multi_selections.is_empty());
}

#[test]
fn space_is_ignored_within_the_grace_period() {
    let mut app = open_question(true);
    app.question_app.mount_time = Some(Instant::now());

    press(&mut app, KeyCode::Char(' '));
    assert!(ticked(&app).is_empty());

    app.question_app.mount_time = None;
    press(&mut app, KeyCode::Char(' '));
    assert_eq!(ticked(&app), BTreeSet::from([0]));
}

#[test]
fn modified_space_does_not_toggle() {
    let mut app = open_question(true);
    let client = Arc::new(Client::stub());

    for modifiers in [
        KeyModifiers::CONTROL,
        KeyModifiers::ALT,
        KeyModifiers::SHIFT,
    ] {
        handle_key(
            &mut app,
            &client,
            KeyEvent::new(KeyCode::Char(' '), modifiers),
        );
    }

    assert!(ticked(&app).is_empty());
}
