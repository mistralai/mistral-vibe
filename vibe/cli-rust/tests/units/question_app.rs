//! Question viewport, pointer-selection and paste state transitions.

use vibe_rs::question_app::{move_down, OtherField};

use crate::support::{
    down, drag, question_mouse, question_region, set_question_rows, up, with_question,
};

#[test]
fn keyboard_navigation_reattaches_the_viewport() {
    let mut app = with_question(false);
    app.question_app.list.detach_at(8);

    move_down(&mut app);

    assert_eq!(app.question_app.list.selected(), 1);
    assert!(!app.question_app.list.free_scroll());
}

#[test]
fn clicking_a_wrapped_row_keeps_the_viewport_detached() {
    let mut app = with_question(false);
    app.question_app.other_texts.insert(0, "custom".into());
    app.question_app.list.detach_at(8);
    set_question_rows(&mut app, &[(7, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    question_mouse(&mut app, down(3, 7));
    question_mouse(&mut app, up(3, 7));

    assert_eq!(app.question_app.list.selected(), 1);
    assert_eq!(app.question_app.other_cursor, "custom".len());
    assert!(app.question_app.list.free_scroll());
}

#[test]
fn a_drag_from_an_option_row_selects_instead_of_clicking() {
    let mut app = with_question(false);
    set_question_rows(&mut app, &[(7, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    question_mouse(&mut app, down(3, 7));
    question_mouse(&mut app, drag(30, 9));
    question_mouse(&mut app, up(30, 9));

    assert_eq!(
        app.selection.region.as_ref().map(|sel| sel.pending_copy),
        Some(true)
    );
    assert_eq!(app.question_app.list.selected(), 0);
}

#[test]
fn a_press_on_the_border_never_selects_or_clicks() {
    let mut app = with_question(false);
    set_question_rows(&mut app, &[(7, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    question_mouse(&mut app, down(0, 5));
    question_mouse(&mut app, drag(20, 5));
    question_mouse(&mut app, up(20, 5));

    assert!(app.selection.region.is_none());
    assert_eq!(app.question_app.list.selected(), 0);
}

#[test]
fn one_click_ticks_a_multi_select_option() {
    let mut app = with_question(true);
    set_question_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    question_mouse(&mut app, down(3, 8));
    question_mouse(&mut app, up(3, 8));

    assert_eq!(app.question_app.list.selected(), 1);
    assert!(app.question_app.multi_selections[&0].contains(&1));
}

#[test]
fn one_click_answers_a_single_select_option() {
    let mut app = with_question(false);
    app.question_app
        .questions
        .push(app.question_app.questions[0].clone());
    set_question_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    question_mouse(&mut app, down(3, 8));
    question_mouse(&mut app, up(3, 8));

    assert_eq!(app.question_app.answers[&0].0, "Beta");
    assert_eq!(app.question_app.current_question_idx, 1);
}

#[test]
fn a_double_click_on_an_option_toggles_twice_without_selecting_a_word() {
    let mut app = with_question(true);
    set_question_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    for _ in 0..2 {
        question_mouse(&mut app, down(3, 8));
        question_mouse(&mut app, up(3, 8));
    }

    assert!(!app.question_app.multi_selections[&0].contains(&1));
    assert!(app.selection.region.is_none());
}

#[test]
fn a_jittered_round_trip_still_clicks() {
    let mut app = with_question(false);
    set_question_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    question_mouse(&mut app, down(3, 8));
    question_mouse(&mut app, drag(4, 8));
    question_mouse(&mut app, drag(3, 8));
    question_mouse(&mut app, up(3, 8));

    assert_eq!(app.question_app.list.selected(), 1);
}

#[test]
fn the_free_text_row_owns_its_mouse() {
    let mut app = with_question(false);
    app.question_app.other_texts.insert(0, "draft".into());
    set_question_rows(&mut app, &[(7, 0), (8, 1), (9, 2)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    question_mouse(&mut app, down(3, 9));
    question_mouse(&mut app, drag(10, 9));
    question_mouse(&mut app, up(3, 9));

    assert!(app.selection.region.is_none());
    assert_eq!(app.question_app.list.selected(), 2);
    assert_eq!(app.question_app.other_cursor, "draft".len());
}

#[test]
fn a_click_on_the_focused_free_text_row_never_answers() {
    for mut app in [with_question(false), with_question(true)] {
        app.question_app.other_texts.insert(0, "draft".into());
        app.question_app
            .multi_selections
            .entry(0)
            .or_default()
            .insert(2);
        set_question_rows(&mut app, &[(7, 0), (8, 1), (9, 2)]);
        app.question_app.list.select(2);
        app.question_app.other_field = Some(OtherField {
            x: 2,
            y: 9,
            text_width: 40,
        });
        app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

        question_mouse(&mut app, down(3, 9));
        question_mouse(&mut app, up(3, 9));

        assert!(app.question_app.answers.is_empty());
        assert!(app.question_app.multi_selections[&0].contains(&2));
    }
}
