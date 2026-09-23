//! Question viewport, pointer-selection and paste state transitions.

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use vibe_rs::app::App;
use vibe_rs::question_app::{move_down, reconcile_scroll, visible_option_rows, Viewport};
use vibe_rs::question_input::handle_mouse;
use vibe_rs::selection::Region;
use vibe_rs::server::{QuestionChoice, UserQuestion};

/// The box-content region the question draw publishes, as the render would.
fn question_region(x: u16, y: u16, width: u16, height: u16) -> Region {
    Region {
        area: Rect {
            x,
            y,
            width,
            height,
        },
        top: i32::from(y),
        ..Region::default()
    }
}

fn mouse(kind: MouseEventKind, column: u16, row: u16) -> MouseEvent {
    MouseEvent {
        kind,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    }
}

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
fn detached_scroll_is_clamped_to_the_last_viewport() {
    let viewport = Viewport {
        offset: u16::MAX,
        detached: true,
    };

    assert_eq!(reconcile_scroll(viewport, 0, &[], 30, 10), 20);
}

#[test]
fn keyboard_navigation_reveals_every_row_of_a_wrapped_option() {
    let option_rows = [(2, 0), (8, 1), (9, 1), (10, 1), (12, 2)];

    assert_eq!(
        reconcile_scroll(Viewport::default(), 1, &option_rows, 20, 5),
        6
    );
}

#[test]
fn oversized_selected_option_anchors_at_its_first_row() {
    let option_rows = [(5, 1), (6, 1), (7, 1), (8, 1), (9, 1), (10, 1)];

    assert_eq!(
        reconcile_scroll(Viewport::default(), 1, &option_rows, 20, 3),
        5
    );
}

#[test]
fn hit_map_skips_rows_above_the_viewport_without_underflow() {
    let option_rows = [(1, 0), (10, 1), (11, 1), (12, 2)];

    assert_eq!(
        visible_option_rows(&option_rows, 20, 10, 2),
        vec![(21, 1), (22, 1)]
    );
}

#[test]
fn keyboard_navigation_reattaches_the_viewport() {
    let mut app = app_with_question();
    app.question_app.viewport.detach_at(8);

    move_down(&mut app);

    assert_eq!(app.question_app.selected_option, 1);
    assert!(!app.question_app.viewport.detached);
}

#[test]
fn clicking_a_wrapped_row_keeps_the_viewport_detached() {
    let mut app = app_with_question();
    app.question_app.other_texts.insert(0, "custom".into());
    app.question_app.viewport.detach_at(8);
    app.question_app.option_rows = vec![(7, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 7),
    );
    handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 7));

    assert_eq!(app.question_app.selected_option, 1);
    assert_eq!(app.question_app.other_cursor, "custom".len());
    assert!(app.question_app.viewport.detached);
}

#[test]
fn a_drag_from_an_option_row_selects_instead_of_clicking() {
    let mut app = app_with_question();
    app.question_app.option_rows = vec![(7, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 7),
    );
    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Drag(MouseButton::Left), 30, 9),
    );
    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Up(MouseButton::Left), 30, 9),
    );

    assert_eq!(
        app.selection.region.as_ref().map(|sel| sel.pending_copy),
        Some(true)
    );
    assert_eq!(app.question_app.selected_option, 0);
}

#[test]
fn a_press_on_the_border_never_selects_or_clicks() {
    let mut app = app_with_question();
    app.question_app.option_rows = vec![(7, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 0, 5),
    );
    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Drag(MouseButton::Left), 20, 5),
    );
    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Up(MouseButton::Left), 20, 5),
    );

    assert!(app.selection.region.is_none());
    assert_eq!(app.question_app.selected_option, 0);
}

#[test]
fn a_multi_select_click_ticks_the_clicked_row_on_release() {
    let mut app = app_with_multi_select_question();
    app.question_app.option_rows = vec![(7, 0), (8, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 8),
    );
    handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 8));

    assert_eq!(app.question_app.selected_option, 1);
    assert!(app.question_app.multi_selections[&0].contains(&1));
}

#[test]
fn every_click_of_a_double_click_acts_like_python_on_click() {
    let mut app = app_with_multi_select_question();
    app.question_app.option_rows = vec![(7, 0), (8, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);

    for _ in 0..2 {
        handle_mouse(
            &mut app,
            mouse(MouseEventKind::Down(MouseButton::Left), 3, 8),
        );
        handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 8));
    }

    assert!(!app.question_app.multi_selections[&0].contains(&1));
}

#[test]
fn a_jittered_round_trip_still_clicks() {
    let mut app = app_with_question();
    app.question_app.option_rows = vec![(7, 0), (8, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 8),
    );
    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Drag(MouseButton::Left), 4, 8),
    );
    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Drag(MouseButton::Left), 3, 8),
    );
    handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 8));

    assert_eq!(app.question_app.selected_option, 1);
}

#[test]
fn the_free_text_row_owns_its_mouse_like_python_input() {
    let mut app = app_with_question();
    app.question_app.other_texts.insert(0, "draft".into());
    app.question_app.option_rows = vec![(7, 0), (8, 1), (9, 2)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 9),
    );
    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Drag(MouseButton::Left), 10, 9),
    );
    handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 9));

    assert!(app.selection.region.is_none());
    assert_eq!(app.question_app.selected_option, 2);
    assert_eq!(app.question_app.other_cursor, "draft".len());
}
