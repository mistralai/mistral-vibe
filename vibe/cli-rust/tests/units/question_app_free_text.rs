//! Multi-line editing, vertical caret moves and click-to-caret in the free-text field.

use crossterm::event::KeyCode;
use vibe_rs::app::App;
use vibe_rs::question_app::{handle_other_key, other_option_idx, OtherField};

use crate::support::{down, key, question, question_mouse, set_question_rows, up, with_questions};

fn click(app: &mut App, column: u16, row: u16) {
    question_mouse(app, down(column, row));
    question_mouse(app, up(column, row));
}

const FIELD: OtherField = OtherField {
    x: 7,
    y: 10,
    text_width: 40,
};

fn app_with_answer(answer: &str, cursor: usize) -> App {
    let mut app = with_questions(vec![question("Choose", &["First"], false)]);
    app.question_app
        .list
        .highlight(other_option_idx(&app).unwrap());
    app.question_app.other_texts.insert(0, answer.into());
    app.question_app.other_cursor = cursor;
    app.question_app.other_field = Some(FIELD);
    app
}

#[test]
fn editing_the_field_reattaches_a_scrolled_away_viewport() {
    let mut app = app_with_answer("ab", 2);
    app.question_app.list.detach_at(9);

    assert!(handle_other_key(&mut app, key(KeyCode::Char('c'))));

    assert!(!app.question_app.list.free_scroll());
}

#[test]
fn up_and_down_move_the_caret_between_lines_before_leaving_the_field() {
    let mut app = app_with_answer("first\nsecond", "first\nsec".len());

    assert!(handle_other_key(&mut app, key(KeyCode::Up)));
    assert_eq!(app.question_app.other_cursor, "fir".len());
    assert!(!handle_other_key(&mut app, key(KeyCode::Up)));

    assert!(handle_other_key(&mut app, key(KeyCode::Down)));
    assert_eq!(app.question_app.other_cursor, "first\nsec".len());
    assert!(!handle_other_key(&mut app, key(KeyCode::Down)));
}

#[test]
fn up_and_down_move_through_soft_wrapped_rows() {
    let answer = "alpha beta gamma delta epsilon zeta eta theta iota kappa";
    let mut app = app_with_answer(answer, answer.len() - 1);

    assert!(handle_other_key(&mut app, key(KeyCode::Up)));
    // Width 40 wraps after `eta `; the caret keeps its column 15 in `kappa`.
    assert_eq!(app.question_app.other_cursor, "alpha beta gamm".len());
    assert!(!handle_other_key(&mut app, key(KeyCode::Up)));
}

#[test]
fn up_from_the_end_of_the_answer_leaves_the_field() {
    let answer = "first\nsecond";
    let mut app = app_with_answer(answer, answer.len());

    assert!(!handle_other_key(&mut app, key(KeyCode::Up)));
    assert_eq!(app.question_app.other_cursor, answer.len());
}

#[test]
fn a_click_on_the_field_parks_the_caret_under_the_pointer() {
    let mut app = app_with_answer("hello\nworld", 0);
    app.question_app.list.highlight(0);
    set_question_rows(&mut app, &[(9, 0), (10, 1), (11, 1)]);

    click(&mut app, FIELD.x + 2, 11);

    assert_eq!(app.question_app.list.selected(), 1);
    assert_eq!(app.question_app.other_cursor, "hello\nwo".len());
}

#[test]
fn a_click_past_the_line_end_parks_the_caret_at_that_end() {
    let mut app = app_with_answer("hello\nworld", 0);
    set_question_rows(&mut app, &[(10, 1), (11, 1)]);

    click(&mut app, FIELD.x + 30, 10);

    assert_eq!(app.question_app.other_cursor, "hello".len());
}

#[test]
fn a_click_on_the_prefix_toggles_a_typed_answer_in_multi_select() {
    let mut app = app_with_answer("draft", 2);
    app.question_app.questions[0].multi_select = true;
    let other_idx = other_option_idx(&app).unwrap();
    app.question_app
        .multi_selections
        .entry(0)
        .or_default()
        .insert(other_idx);
    set_question_rows(&mut app, &[(10, other_idx)]);
    let ticked = |app: &App| app.question_app.multi_selections[&0].contains(&other_idx);

    click(&mut app, FIELD.x - 1, 10);
    assert!(!ticked(&app));
    assert_eq!(app.question_app.other_cursor, 2);

    click(&mut app, FIELD.x - 1, 10);
    assert!(ticked(&app));

    click(&mut app, FIELD.x + 1, 10);
    assert!(ticked(&app));
    assert_eq!(app.question_app.other_cursor, 1);
}

#[test]
fn a_click_on_the_prefix_never_answers_a_single_select_question() {
    let mut app = app_with_answer("draft", 2);
    let other_idx = other_option_idx(&app).unwrap();
    set_question_rows(&mut app, &[(10, other_idx)]);

    click(&mut app, FIELD.x - 1, 10);

    assert!(app.question_app.answers.is_empty());
    assert_eq!(app.question_app.other_cursor, 2);
}

#[test]
fn a_click_on_a_scrolled_field_maps_to_the_hidden_rows_offset() {
    let mut app = app_with_answer("a\nb\nc\nd", 0);
    set_question_rows(&mut app, &[(10, 1), (11, 1)]);
    app.question_app.other_field = Some(OtherField { y: 8, ..FIELD });

    click(&mut app, FIELD.x, 11);

    assert_eq!(app.question_app.other_cursor, "a\nb\nc\n".len());
}
