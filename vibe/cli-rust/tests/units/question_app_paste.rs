//! Paste routing into the question app's free-text row.

use vibe_rs::app::App;
use vibe_rs::question_app::other_option_idx;
use vibe_rs::question_input::handle_paste;
use vibe_rs::server::{QuestionChoice, UserQuestion};

fn app_with_question() -> App {
    let mut app = App::default();
    app.question_app.questions.push(UserQuestion {
        question: "Choose".into(),
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
        multi_select: false,
        hide_other: false,
    });
    app
}

fn app_with_multi_select_question() -> App {
    let mut app = App::default();
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
        multi_select: true,
        hide_other: false,
    });
    app
}

#[test]
fn paste_keeps_only_the_first_line_in_the_free_text_row() {
    let mut app = app_with_question();
    app.question_app.selected_option = other_option_idx(&app).unwrap();

    handle_paste(&mut app, "one\ntwo\nthree".into());

    assert_eq!(app.question_app.other_texts[&0], "one");
    assert_eq!(app.question_app.other_cursor, 3);
    assert_eq!(app.chat_input.input, "");
}

#[test]
fn paste_ticks_the_free_text_row_only_while_it_has_text() {
    let mut app = app_with_multi_select_question();
    let other_idx = other_option_idx(&app).unwrap();
    app.question_app.selected_option = other_idx;

    handle_paste(&mut app, "custom answer".into());
    assert!(app.question_app.multi_selections[&0].contains(&other_idx));

    let mut app = app_with_multi_select_question();
    app.question_app.selected_option = other_idx;

    handle_paste(&mut app, "   \nignored".into());
    assert!(!app.question_app.multi_selections[&0].contains(&other_idx));
}

#[test]
fn paste_on_an_option_row_is_dropped() {
    let mut app = app_with_question();

    handle_paste(&mut app, "one\ntwo".into());

    assert!(app.question_app.other_texts.is_empty());
    assert_eq!(app.chat_input.input, "");
}

#[test]
fn paste_inserts_at_the_cursor_inside_existing_text() {
    let mut app = app_with_question();
    app.question_app.selected_option = other_option_idx(&app).unwrap();
    app.question_app.other_texts.insert(0, "one three".into());
    app.question_app.other_cursor = 4;

    handle_paste(&mut app, "two \nx".into());

    assert_eq!(app.question_app.other_texts[&0], "one two three");
    assert_eq!(app.question_app.other_cursor, 8);
}

#[test]
fn paste_splits_on_every_python_line_boundary() {
    let mut app = app_with_question();
    app.question_app.selected_option = other_option_idx(&app).unwrap();

    handle_paste(&mut app, "one\r\ntwo".into());
    assert_eq!(app.question_app.other_texts[&0], "one");

    app.question_app.other_cursor = 0;
    handle_paste(&mut app, "a\u{2028}b".into());
    assert_eq!(app.question_app.other_texts[&0], "aone");
}

#[test]
fn paste_into_a_question_without_a_free_text_row_is_dropped() {
    let mut app = app_with_question();
    app.question_app.questions[0].hide_other = true;

    handle_paste(&mut app, "one\ntwo".into());

    assert!(app.question_app.other_texts.is_empty());
    assert_eq!(app.chat_input.input, "");
}

#[test]
fn an_empty_paste_leaves_the_free_text_row_untouched() {
    let mut app = app_with_question();
    app.question_app.selected_option = other_option_idx(&app).unwrap();

    handle_paste(&mut app, String::new());

    assert!(app.question_app.other_texts.is_empty());
}
