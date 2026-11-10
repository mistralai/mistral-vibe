//! Tabs on a browser's title row: their labels, and the tab a press lands on.

use crossterm::event::{MouseButton, MouseEventKind};
use ratatui::layout::Rect;
use vibe_rs::tab_bar::{label, pressed};

use crate::support::mouse;

#[test]
fn a_label_counts_its_matches_only_while_searching() {
    assert_eq!(label("Shared", None), "Shared");
    assert_eq!(label("Shared", Some(3)), "Shared 3");
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
