//! Question-box option prefixes are selection chrome: never anchored,
//! highlighted, or copied, while a press on them still clicks its option.

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;
use vibe_rs::app::{App, Selection};
use vibe_rs::question_input::handle_mouse;
use vibe_rs::selection::{region, Granularity, Region, RegionId};
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
                description: "First choice".into(),
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

fn question_selection(anchor: (u16, i32), head: (u16, i32)) -> Selection {
    Selection {
        owner: RegionId::Question,
        anchor,
        head,
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: Default::default(),
        table_cell: None,
        text: String::new(),
    }
}

#[test]
fn a_press_on_an_option_prefix_anchors_no_selection() {
    let mut app = app_with_question();
    app.question_app.option_rows = vec![(7, 0), (8, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);
    app.view.question_selection_chrome = vec![(7, 2, 6), (8, 2, 6)];

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 7),
    );
    assert!(app.selection.region.is_none());
    assert!(app.selection.drag.is_none());

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Drag(MouseButton::Left), 30, 7),
    );
    assert!(app.selection.region.is_none());
    assert!(app.selection.drag.is_none());
}

#[test]
fn a_same_cell_press_on_a_prefix_still_clicks_its_option() {
    let mut app = app_with_question();
    app.question_app.option_rows = vec![(7, 0), (8, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);
    app.view.question_selection_chrome = vec![(7, 2, 6), (8, 2, 6)];

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 8),
    );
    handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 8));

    assert_eq!(app.question_app.selected_option, 1);
}

#[test]
fn a_same_cell_press_on_a_multi_select_prefix_still_ticks_its_option() {
    let mut app = app_with_multi_select_question();
    app.question_app.option_rows = vec![(7, 0), (8, 1)];
    app.view.question_selection_region = question_region(2, 6, 60, 10);
    app.view.question_selection_chrome = vec![(7, 2, 10)];

    handle_mouse(
        &mut app,
        mouse(MouseEventKind::Down(MouseButton::Left), 3, 7),
    );
    handle_mouse(&mut app, mouse(MouseEventKind::Up(MouseButton::Left), 3, 7));

    assert_eq!(app.question_app.selected_option, 0);
    assert!(app.question_app.multi_selections[&0].contains(&0));
}

#[test]
fn a_drag_across_an_option_row_excludes_its_prefix_cells() {
    let chat = Rect::new(2, 6, 40, 2);
    let mut buf = Buffer::empty(Rect::new(0, 0, 44, 8));
    buf.set_string(
        0,
        6,
        "│ Which color scheme do you prefer?",
        Style::default(),
    );
    buf.set_string(
        0,
        7,
        "│ › 1. Solarized - Warm muted tones",
        Style::default(),
    );
    let mut app = app_with_question();
    app.view.question_selection_region = question_region(2, 6, 40, 2);
    app.view.question_selection_chrome = vec![(7, 2, 6)];
    app.selection.region = Some(question_selection((2, 0), (34, 1)));
    app.selection.granularity = Granularity::Char;

    let spans = region::spans(&app, &buf, chat);

    assert_eq!(spans, vec![(6, 2, 34), (7, 7, 34)]);
    let text = region::extract(&buf, &spans);
    assert_eq!(
        text,
        "Which color scheme do you prefer?\nSolarized - Warm muted tones"
    );
}

#[test]
fn a_drag_across_a_multi_select_option_excludes_its_checkbox_prefix() {
    let chat = Rect::new(2, 6, 40, 2);
    let mut buf = Buffer::empty(Rect::new(0, 0, 44, 8));
    buf.set_string(0, 6, "│ Pick one", Style::default());
    buf.set_string(0, 7, "│   1. [ ] Alpha - First choice", Style::default());
    let mut app = app_with_multi_select_question();
    app.view.question_selection_region = question_region(2, 6, 40, 2);
    app.view.question_selection_chrome = vec![(7, 2, 10)];
    app.selection.region = Some(question_selection((2, 0), (30, 1)));
    app.selection.granularity = Granularity::Char;

    let spans = region::spans(&app, &buf, chat);

    assert_eq!(spans, vec![(6, 2, 9), (7, 11, 30)]);
    assert_eq!(
        region::extract(&buf, &spans),
        "Pick one\nAlpha - First choice"
    );
}

#[test]
fn a_drag_across_the_free_text_row_excludes_its_prefix() {
    let chat = Rect::new(2, 6, 40, 3);
    let mut buf = Buffer::empty(Rect::new(0, 0, 44, 9));
    buf.set_string(0, 6, "│ › 1. First", Style::default());
    buf.set_string(0, 7, "│ › 2. Second", Style::default());
    buf.set_string(0, 8, "│ › 3. draft answer", Style::default());
    let mut app = app_with_question();
    app.view.question_selection_region = question_region(2, 6, 40, 3);
    app.view.question_selection_chrome = vec![(6, 2, 6), (7, 2, 6), (8, 2, 6)];
    app.selection.region = Some(question_selection((2, 0), (18, 2)));
    app.selection.granularity = Granularity::Char;

    let spans = region::spans(&app, &buf, chat);

    assert_eq!(spans, vec![(6, 7, 11), (7, 7, 12), (8, 7, 18)]);
    assert_eq!(region::extract(&buf, &spans), "First\nSecond\ndraft answer");
}
