//! Question viewport, pointer-selection and paste state transitions.

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use vibe_rs::app::App;
use vibe_rs::question_app::{move_down, OtherField};
use vibe_rs::selection::Region;
use vibe_rs::server::{QuestionChoice, UserQuestion};

/// Route one mouse event through the question app with a stub app-server client.
fn handle_mouse(app: &mut App, event: crossterm::event::MouseEvent) {
    let client = std::sync::Arc::new(vibe_rs::server::Client::stub());
    vibe_rs::question_input::handle_mouse(app, &client, event);
}

/// Lay out the question's option rows at screen rows, as the render would.
pub(crate) fn set_rows(app: &mut App, rows: &[(u16, usize)]) {
    let height = rows.iter().map(|(row, _)| row + 1).max().unwrap_or(0);
    let mut lines = vec![None; usize::from(height)];
    for (row, option) in rows {
        lines[usize::from(*row)] = Some(*option);
    }
    app.question_app
        .list
        .layout(&lines, Rect::new(0, 0, 80, height));
}

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
fn keyboard_navigation_reattaches_the_viewport() {
    let mut app = app_with_question();
    app.question_app.list.detach_at(8);

    move_down(&mut app);

    assert_eq!(app.question_app.list.selected(), 1);
    assert!(!app.question_app.list.free_scroll());
}

#[test]
fn clicking_a_wrapped_row_keeps_the_viewport_detached() {
    let mut app = app_with_question();
    app.question_app.other_texts.insert(0, "custom".into());
    app.question_app.list.detach_at(8);
    set_rows(&mut app, &[(7, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 7),
    );
    handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 7));

    assert_eq!(app.question_app.list.selected(), 1);
    assert_eq!(app.question_app.other_cursor, "custom".len());
    assert!(app.question_app.list.free_scroll());
}

#[test]
fn a_drag_from_an_option_row_selects_instead_of_clicking() {
    let mut app = app_with_question();
    set_rows(&mut app, &[(7, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

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
    assert_eq!(app.question_app.list.selected(), 0);
}

#[test]
fn a_press_on_the_border_never_selects_or_clicks() {
    let mut app = app_with_question();
    set_rows(&mut app, &[(7, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

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
    assert_eq!(app.question_app.list.selected(), 0);
}

#[test]
fn one_click_ticks_a_multi_select_option() {
    let mut app = app_with_multi_select_question();
    set_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 8),
    );
    handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 8));

    assert_eq!(app.question_app.list.selected(), 1);
    assert!(app.question_app.multi_selections[&0].contains(&1));
}

#[test]
fn one_click_answers_a_single_select_option() {
    let mut app = app_with_question();
    app.question_app
        .questions
        .push(app.question_app.questions[0].clone());
    set_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 8),
    );
    handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 8));

    assert_eq!(app.question_app.answers[&0].0, "Second");
    assert_eq!(app.question_app.current_question_idx, 1);
}

#[test]
fn a_double_click_on_an_option_toggles_twice_without_selecting_a_word() {
    let mut app = app_with_multi_select_question();
    set_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

    for _ in 0..2 {
        handle_mouse(
            &mut app,
            mouse(MouseEventKind::Down(MouseButton::Left), 3, 8),
        );
        handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 8));
    }

    assert!(!app.question_app.multi_selections[&0].contains(&1));
    assert!(app.selection.region.is_none());
}

#[test]
fn a_jittered_round_trip_still_clicks() {
    let mut app = app_with_question();
    set_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

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

    assert_eq!(app.question_app.list.selected(), 1);
}

#[test]
fn the_free_text_row_owns_its_mouse() {
    let mut app = app_with_question();
    app.question_app.other_texts.insert(0, "draft".into());
    set_rows(&mut app, &[(7, 0), (8, 1), (9, 2)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

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
    assert_eq!(app.question_app.list.selected(), 2);
    assert_eq!(app.question_app.other_cursor, "draft".len());
}

#[test]
fn a_click_on_the_focused_free_text_row_never_answers() {
    for mut app in [app_with_question(), app_with_multi_select_question()] {
        app.question_app.other_texts.insert(0, "draft".into());
        app.question_app
            .multi_selections
            .entry(0)
            .or_default()
            .insert(2);
        set_rows(&mut app, &[(7, 0), (8, 1), (9, 2)]);
        app.question_app.list.select(2);
        app.question_app.other_field = Some(OtherField {
            x: 2,
            y: 9,
            text_width: 40,
        });
        app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);

        handle_mouse(
            &mut app,
            mouse(MouseEventKind::Down(MouseButton::Left), 3, 9),
        );
        handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 9));

        assert!(app.question_app.answers.is_empty());
        assert!(app.question_app.multi_selections[&0].contains(&2));
    }
}
