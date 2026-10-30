//! Row and question navigation in the question app, and what switching questions restores.

use crossterm::event::KeyCode;
use std::collections::BTreeSet;
use vibe_rs::question_app::{is_submit_selected, other_option_idx, submit_option_idx};

use crate::support::{press_question as press, question, with_questions};

#[test]
fn arrows_and_j_k_wrap_over_the_options_and_the_free_text_row() {
    let mut app = with_questions(vec![question("Pick", &["Alpha", "Beta"], false)]);

    press(&mut app, KeyCode::Up);
    assert_eq!(
        app.question_app.list.selected(),
        2,
        "wraps to the free-text row"
    );
    press(&mut app, KeyCode::Down);
    assert_eq!(app.question_app.list.selected(), 0, "wraps back to the top");
    press(&mut app, KeyCode::Char('j'));
    assert_eq!(app.question_app.list.selected(), 1);
    press(&mut app, KeyCode::Char('k'));
    assert_eq!(app.question_app.list.selected(), 0);
    assert!(app.question_app.answers.is_empty());
}

#[test]
fn multi_select_reaches_the_submit_row_and_wraps_past_it() {
    let mut app = with_questions(vec![question("Pick", &["Alpha", "Beta"], true)]);

    press(&mut app, KeyCode::Up);
    assert!(is_submit_selected(&app));
    assert_eq!(submit_option_idx(&app), Some(3));
    press(&mut app, KeyCode::Down);
    assert_eq!(app.question_app.list.selected(), 0);
}

#[test]
fn more_than_four_options_are_navigable_and_numbered() {
    let labels = ["One", "Two", "Three", "Four", "Five", "Six"];
    let mut app = with_questions(vec![
        question("Pick", &labels, false),
        question("Pick", &["Next"], false),
    ]);

    press(&mut app, KeyCode::Up);
    assert_eq!(other_option_idx(&app), Some(6));
    assert_eq!(app.question_app.list.selected(), 6);
    press(&mut app, KeyCode::Down);

    press(&mut app, KeyCode::Char('6'));
    assert_eq!(
        app.question_app.answers.get(&0),
        Some(&("Six".into(), false))
    );
}

#[test]
fn switching_questions_keeps_each_free_text_answer() {
    let mut app = with_questions(vec![
        question("Pick", &["Alpha"], false),
        question("Pick", &["Beta"], false),
    ]);
    app.question_app.other_texts.insert(0, "first".into());
    app.question_app.other_texts.insert(1, "second".into());

    press(&mut app, KeyCode::Right);
    assert_eq!(app.question_app.current_question_idx, 1);
    press(&mut app, KeyCode::Left);
    assert_eq!(app.question_app.current_question_idx, 0);

    assert_eq!(app.question_app.other_texts.get(&0).unwrap(), "first");
    assert_eq!(app.question_app.other_texts.get(&1).unwrap(), "second");
}

#[test]
fn left_and_right_wrap_across_questions() {
    let mut app = with_questions(vec![
        question("Pick", &["Alpha"], false),
        question("Pick", &["Beta"], false),
        question("Pick", &["Gamma"], false),
    ]);

    press(&mut app, KeyCode::Left);
    assert_eq!(app.question_app.current_question_idx, 2);
    press(&mut app, KeyCode::Right);
    assert_eq!(app.question_app.current_question_idx, 0);
}

#[test]
fn returning_to_an_answered_question_restores_its_option() {
    let mut app = with_questions(vec![
        question("Pick", &["Alpha", "Beta"], false),
        question("Pick", &["Gamma"], false),
    ]);
    press(&mut app, KeyCode::Char('2'));
    assert_eq!(app.question_app.current_question_idx, 1);
    assert_eq!(
        app.question_app.list.selected(),
        0,
        "unanswered starts at the top"
    );

    press(&mut app, KeyCode::Left);

    assert_eq!(app.question_app.current_question_idx, 0);
    assert_eq!(app.question_app.list.selected(), 1);
}

#[test]
fn returning_to_a_free_text_answer_restores_the_free_text_row() {
    let mut app = with_questions(vec![
        question("Pick", &["Alpha", "Beta"], false),
        question("Pick", &["Gamma"], false),
    ]);
    app.question_app.other_texts.insert(0, "custom".into());
    press(&mut app, KeyCode::Char('3'));
    press(&mut app, KeyCode::Enter);
    assert_eq!(
        app.question_app.answers.get(&0),
        Some(&("custom".into(), true))
    );
    assert_eq!(app.question_app.current_question_idx, 1);

    press(&mut app, KeyCode::Left);

    assert_eq!(app.question_app.list.selected(), 2);
}

#[test]
fn returning_to_a_multi_select_question_restores_its_first_tick() {
    let mut app = with_questions(vec![
        question("Pick", &["Alpha", "Beta", "Gamma"], true),
        question("Pick", &["Delta"], false),
    ]);
    app.question_app
        .multi_selections
        .insert(0, BTreeSet::from([2, 1]));

    press(&mut app, KeyCode::Right);
    assert_eq!(app.question_app.list.selected(), 0);
    press(&mut app, KeyCode::Left);

    assert_eq!(app.question_app.list.selected(), 1);
}
