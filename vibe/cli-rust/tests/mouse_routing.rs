//! Paint-order hit testing and press-to-release ownership.

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;

use vibe_rs::app::App;
use vibe_rs::mouse::{self, MouseTarget};

#[test]
fn a_bottom_panel_only_owns_its_painted_rectangle() {
    let mut app = App::default();
    mouse::register_region(&mut app, Rect::new(0, 0, 20, 10), MouseTarget::Blocked);
    mouse::register_region(&mut app, Rect::new(0, 0, 20, 6), MouseTarget::Transcript);
    mouse::register_region(&mut app, Rect::new(0, 6, 20, 4), MouseTarget::Question);

    assert_eq!(
        mouse::target_at(&app, (5, 4)),
        Some(MouseTarget::Transcript)
    );
    assert_eq!(mouse::target_at(&app, (5, 7)), Some(MouseTarget::Question));
}

#[test]
fn a_left_button_gesture_keeps_its_starting_owner() {
    let mut app = App::default();
    mouse::register_region(&mut app, Rect::new(0, 0, 20, 6), MouseTarget::Transcript);
    mouse::register_region(&mut app, Rect::new(0, 6, 20, 4), MouseTarget::Mcp);

    assert_eq!(
        mouse::route(
            &mut app,
            event(MouseEventKind::Down(MouseButton::Left), 5, 7)
        ),
        Some(MouseTarget::Mcp)
    );
    assert_eq!(
        mouse::route(
            &mut app,
            event(MouseEventKind::Drag(MouseButton::Left), 5, 4)
        ),
        Some(MouseTarget::Mcp)
    );
    assert_eq!(
        mouse::route(&mut app, event(MouseEventKind::Up(MouseButton::Left), 5, 4)),
        Some(MouseTarget::Mcp)
    );
    assert_eq!(
        mouse::route(&mut app, event(MouseEventKind::Up(MouseButton::Left), 5, 4)),
        None
    );
}

#[test]
fn resume_picker_ignores_wheel_while_loading() {
    let mut app = App::default();
    mouse::register_region(&mut app, Rect::new(0, 0, 20, 10), MouseTarget::ResumePicker);

    assert_eq!(
        mouse::route(&mut app, event(MouseEventKind::ScrollDown, 5, 5)),
        None
    );
    assert_eq!(app.resume_picker.scroll, 0);
    assert!(!app.resume_picker.free_scroll);
}

#[test]
fn every_painted_region_remains_routable() {
    let mut app = App::default();
    let area = Rect::new(0, 0, 20, 10);
    for _ in 0..32 {
        mouse::register_region(&mut app, area, MouseTarget::Transcript);
    }
    mouse::register_region(&mut app, area, MouseTarget::Question);

    assert_eq!(mouse::target_at(&app, (5, 5)), Some(MouseTarget::Question));
}

#[test]
fn scrollbar_registration_is_not_capped_by_other_regions() {
    let mut app = App::default();
    for row in 0..32 {
        mouse::register_region(&mut app, Rect::new(0, row, 20, 1), MouseTarget::Transcript);
    }
    mouse::register_scrollbar(
        &mut app,
        MouseTarget::Mcp,
        Rect::new(30, 0, 1, 10),
        20,
        10,
        0,
    );

    assert_eq!(
        mouse::route(
            &mut app,
            event(MouseEventKind::Down(MouseButton::Left), 30, 0)
        ),
        None
    );
    assert!(app.view.mouse.is_dragging_scrollbar());
}

fn event(kind: MouseEventKind, column: u16, row: u16) -> MouseEvent {
    MouseEvent {
        kind,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    }
}
