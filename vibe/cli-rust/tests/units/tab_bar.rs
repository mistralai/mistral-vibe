//! Tabs on a browser's title row: their labels, and the tab a press lands on.

use crossterm::event::{MouseButton, MouseEventKind};
use ratatui::layout::Rect;
use vibe_rs::tab_bar::{pressed, Label};

use crate::support::mouse;

#[test]
fn a_label_reads_as_its_name_then_its_count() {
    let label = |count| Label {
        name: "Shared",
        count,
    };
    assert_eq!(label(None).text(), "Shared");
    assert_eq!(label(Some(3)).text(), "Shared 3");
}

#[test]
fn only_a_left_press_inside_a_tab_lands_on_it() {
    let areas = [(1, Rect::new(10, 3, 6, 1)), (2, Rect::new(18, 3, 8, 1))];
    let at = |kind, column| pressed(&areas, &mouse(kind, column, 3));
    assert_eq!(at(MouseEventKind::Down(MouseButton::Left), 19), Some(2));
    assert_eq!(at(MouseEventKind::Down(MouseButton::Left), 16), None);
    assert_eq!(at(MouseEventKind::Down(MouseButton::Right), 19), None);
    assert_eq!(at(MouseEventKind::Up(MouseButton::Left), 19), None);
}
