//! Pointer selection on questions that hide the free-text row.

use crate::support::{
    down, drag, question, question_mouse, question_region, set_question_rows, up, with_questions,
};

fn app_with_hide_other_question() -> vibe_rs::app::App {
    let mut question = question("Choose", &["First", "Second"], false);
    question.hide_other = true;
    with_questions(vec![question])
}

#[test]
fn a_hide_other_drag_from_a_non_option_row_selects() {
    let mut app = app_with_hide_other_question();
    set_question_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    question_mouse(&mut app, down(3, 6));
    question_mouse(&mut app, drag(30, 6));
    question_mouse(&mut app, up(30, 6));

    assert_eq!(
        app.selection.region.as_ref().map(|sel| sel.pending_copy),
        Some(true)
    );
    assert_eq!(app.question_app.list.selected(), 0);
}

#[test]
fn a_hide_other_release_on_a_non_option_row_never_clicks() {
    let mut app = app_with_hide_other_question();
    set_question_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    question_mouse(&mut app, down(3, 6));
    question_mouse(&mut app, up(3, 6));

    assert!(app.selection.region.is_none());
    assert_eq!(app.question_app.list.selected(), 0);
}
