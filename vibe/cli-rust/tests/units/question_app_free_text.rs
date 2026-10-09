//! Multi-line editing, vertical caret moves and click-to-caret in the free-text field.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use vibe_rs::app::App;
use vibe_rs::question_app::{handle_other_key, other_option_idx, OtherField};
use vibe_rs::question_input::handle_mouse;
use vibe_rs::server::{QuestionChoice, UserQuestion};

const FIELD: OtherField = OtherField {
    x: 7,
    y: 10,
    text_width: 40,
};

fn app_with_answer(answer: &str, cursor: usize) -> App {
    let mut app = App::default();
    app.question_app.questions.push(UserQuestion {
        question: "Choose".into(),
        header: String::new(),
        options: vec![QuestionChoice {
            label: "First".into(),
            description: String::new(),
        }],
        multi_select: false,
        hide_other: false,
    });
    app.question_app.selected_option = other_option_idx(&app).unwrap();
    app.question_app.other_texts.insert(0, answer.into());
    app.question_app.other_cursor = cursor;
    app.question_app.other_field = Some(FIELD);
    app
}

fn key(code: KeyCode, modifiers: KeyModifiers) -> KeyEvent {
    KeyEvent::new(code, modifiers)
}

fn click(app: &mut App, column: u16, row: u16) {
    for kind in [
        MouseEventKind::Down(MouseButton::Left),
        MouseEventKind::Up(MouseButton::Left),
    ] {
        let event = MouseEvent {
            kind,
            column,
            row,
            modifiers: KeyModifiers::NONE,
        };
        handle_mouse(app, event);
    }
}

#[test]
fn ctrl_j_and_shift_enter_insert_a_newline_but_enter_does_not() {
    let mut app = app_with_answer("ab", 2);

    assert!(handle_other_key(
        &mut app,
        key(KeyCode::Char('j'), KeyModifiers::CONTROL)
    ));
    assert!(handle_other_key(
        &mut app,
        key(KeyCode::Enter, KeyModifiers::SHIFT)
    ));
    assert!(!handle_other_key(
        &mut app,
        key(KeyCode::Enter, KeyModifiers::NONE)
    ));

    assert_eq!(app.question_app.other_texts[&0], "ab\n\n");
}

#[test]
fn esc_falls_through_to_the_question_app() {
    let mut app = app_with_answer("ab", 2);

    assert!(!handle_other_key(
        &mut app,
        key(KeyCode::Esc, KeyModifiers::NONE)
    ));
}

#[test]
fn editing_the_field_reattaches_a_scrolled_away_viewport() {
    let mut app = app_with_answer("ab", 2);
    app.question_app.viewport.detach_at(9);

    assert!(handle_other_key(
        &mut app,
        key(KeyCode::Char('c'), KeyModifiers::NONE)
    ));

    assert!(!app.question_app.viewport.detached);
}

#[test]
fn clearing_the_field_unticks_it_in_multi_select() {
    let mut app = app_with_answer("", 0);
    app.question_app.questions[0].multi_select = true;
    let other_idx = other_option_idx(&app).unwrap();

    handle_other_key(&mut app, key(KeyCode::Char('a'), KeyModifiers::NONE));
    assert!(app.question_app.multi_selections[&0].contains(&other_idx));

    handle_other_key(&mut app, key(KeyCode::Char('w'), KeyModifiers::CONTROL));
    assert!(!app.question_app.multi_selections[&0].contains(&other_idx));
}

#[test]
fn up_and_down_move_the_caret_between_lines_before_leaving_the_field() {
    let mut app = app_with_answer("first\nsecond", "first\nsec".len());

    assert!(handle_other_key(
        &mut app,
        key(KeyCode::Up, KeyModifiers::NONE)
    ));
    assert_eq!(app.question_app.other_cursor, "fir".len());
    assert!(!handle_other_key(
        &mut app,
        key(KeyCode::Up, KeyModifiers::NONE)
    ));

    assert!(handle_other_key(
        &mut app,
        key(KeyCode::Down, KeyModifiers::NONE)
    ));
    assert_eq!(app.question_app.other_cursor, "first\nsec".len());
    assert!(!handle_other_key(
        &mut app,
        key(KeyCode::Down, KeyModifiers::NONE)
    ));
}

#[test]
fn up_and_down_move_through_soft_wrapped_rows() {
    let answer = "alpha beta gamma delta epsilon zeta eta theta iota kappa";
    let mut app = app_with_answer(answer, answer.len() - 1);

    assert!(handle_other_key(
        &mut app,
        key(KeyCode::Up, KeyModifiers::NONE)
    ));
    // Width 40 wraps after `eta `; the caret keeps its column 15 in `kappa`.
    assert_eq!(app.question_app.other_cursor, "alpha beta gamm".len());
    assert!(!handle_other_key(
        &mut app,
        key(KeyCode::Up, KeyModifiers::NONE)
    ));
}

#[test]
fn up_from_the_end_of_the_answer_leaves_the_field() {
    let answer = "first\nsecond";
    let mut app = app_with_answer(answer, answer.len());

    assert!(!handle_other_key(
        &mut app,
        key(KeyCode::Up, KeyModifiers::NONE)
    ));
    assert_eq!(app.question_app.other_cursor, answer.len());
}

#[test]
fn a_click_on_the_field_parks_the_caret_under_the_pointer() {
    let mut app = app_with_answer("hello\nworld", 0);
    app.question_app.selected_option = 0;
    app.question_app.option_rows = vec![(9, 0), (10, 1), (11, 1)];

    click(&mut app, FIELD.x + 2, 11);

    assert_eq!(app.question_app.selected_option, 1);
    assert_eq!(app.question_app.other_cursor, "hello\nwo".len());
}

#[test]
fn a_click_past_the_line_end_parks_the_caret_at_that_end() {
    let mut app = app_with_answer("hello\nworld", 0);
    app.question_app.option_rows = vec![(10, 1), (11, 1)];

    click(&mut app, FIELD.x + 30, 10);

    assert_eq!(app.question_app.other_cursor, "hello".len());
}

#[test]
fn a_click_on_a_scrolled_field_maps_to_the_hidden_rows_offset() {
    let mut app = app_with_answer("a\nb\nc\nd", 0);
    app.question_app.option_rows = vec![(10, 1), (11, 1)];
    app.question_app.other_field = Some(OtherField { y: 8, ..FIELD });

    click(&mut app, FIELD.x, 11);

    assert_eq!(app.question_app.other_cursor, "a\nb\nc\n".len());
}
