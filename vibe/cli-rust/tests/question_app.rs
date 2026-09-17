//! Question viewport and pointer-selection state transitions.

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use vibe_rs::app::App;
use vibe_rs::question_app::{move_down, reconcile_scroll, visible_option_rows, Viewport};
use vibe_rs::question_input::handle_mouse;
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

    handle_mouse(
        &mut app,
        MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 3,
            row: 7,
            modifiers: KeyModifiers::NONE,
        },
    );

    assert_eq!(app.question_app.selected_option, 1);
    assert_eq!(app.question_app.other_cursor, "custom".len());
    assert!(app.question_app.viewport.detached);
}
