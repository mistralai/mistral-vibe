//! Pointer selection on questions that hide the free-text row.

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use vibe_rs::app::App;
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

fn app_with_hide_other_question() -> App {
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
        hide_other: true,
    });
    app
}

#[test]
fn a_hide_other_drag_from_a_non_option_row_selects() {
    let mut app = app_with_hide_other_question();
    app.question_app.option_rows = vec![(7, 0), (8, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 6),
    );
    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Drag(MouseButton::Left), 30, 6),
    );
    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Up(MouseButton::Left), 30, 6),
    );

    assert_eq!(
        app.selection.region.as_ref().map(|sel| sel.pending_copy),
        Some(true)
    );
    assert_eq!(app.question_app.selected_option, 0);
}

#[test]
fn a_hide_other_release_on_a_non_option_row_never_clicks() {
    let mut app = app_with_hide_other_question();
    app.question_app.option_rows = vec![(7, 0), (8, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 6),
    );
    handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 6));

    assert!(app.selection.region.is_none());
    assert_eq!(app.question_app.selected_option, 0);
}
