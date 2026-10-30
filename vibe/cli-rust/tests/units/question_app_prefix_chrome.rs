//! Question-box option prefixes are selection chrome: never anchored,
//! highlighted, or copied, while a press on them still clicks its option.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;
use vibe_rs::app::Selection;
use vibe_rs::selection::{region, Granularity, RegionId};

use crate::support::{
    down, drag, question_mouse, question_region, set_question_rows, up, with_question,
};

fn question_selection(anchor: (u16, i32), head: (u16, i32)) -> Selection {
    Selection {
        owner: RegionId::BottomApp,
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
    let mut app = with_question(false);
    set_question_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);
    app.view.bottom_app_selection_chrome = vec![(7, 2, 6), (8, 2, 6)];

    question_mouse(&mut app, down(3, 7));
    assert!(app.selection.region.is_none());
    assert!(app.selection.drag.is_none());

    question_mouse(&mut app, drag(30, 7));
    assert!(app.selection.region.is_none());
    assert!(app.selection.drag.is_none());
}

#[test]
fn a_same_cell_press_on_a_prefix_still_clicks_its_option() {
    let mut app = with_question(false);
    set_question_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);
    app.view.bottom_app_selection_chrome = vec![(7, 2, 6), (8, 2, 6)];

    question_mouse(&mut app, down(3, 8));
    question_mouse(&mut app, up(3, 8));

    assert_eq!(app.question_app.list.selected(), 1);
}

#[test]
fn a_same_cell_press_on_a_multi_select_prefix_still_ticks_its_option() {
    let mut app = with_question(true);
    set_question_rows(&mut app, &[(7, 0), (8, 1)]);
    app.view.bottom_app_selection_region = question_region(2, 6, 60, 10);
    app.view.bottom_app_selection_chrome = vec![(7, 2, 10)];

    question_mouse(&mut app, down(3, 7));
    question_mouse(&mut app, up(3, 7));

    assert_eq!(app.question_app.list.selected(), 0);
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
    let mut app = with_question(false);
    app.view.bottom_app_selection_region = question_region(2, 6, 40, 2);
    app.view.bottom_app_selection_chrome = vec![(7, 2, 6)];
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
    let mut app = with_question(true);
    app.view.bottom_app_selection_region = question_region(2, 6, 40, 2);
    app.view.bottom_app_selection_chrome = vec![(7, 2, 10)];
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
    let mut app = with_question(false);
    app.view.bottom_app_selection_region = question_region(2, 6, 40, 3);
    app.view.bottom_app_selection_chrome = vec![(6, 2, 6), (7, 2, 6), (8, 2, 6)];
    app.selection.region = Some(question_selection((2, 0), (18, 2)));
    app.selection.granularity = Granularity::Char;

    let spans = region::spans(&app, &buf, chat);

    assert_eq!(spans, vec![(6, 7, 11), (7, 7, 12), (8, 7, 18)]);
    assert_eq!(region::extract(&buf, &spans), "First\nSecond\ndraft answer");
}
