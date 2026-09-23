//! Scrollbar drag behavior independent of terminal rendering.

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;

use vibe_rs::{app::App, mouse};

#[test]
fn scrollbar_drag_tracks_the_pointer_directly() {
    let mut app = App::default();
    mouse::register_region(
        &mut app,
        Rect::new(0, 0, 11, 10),
        mouse::MouseTarget::Transcript,
    );
    mouse::register_scrollbar(
        &mut app,
        mouse::MouseTarget::Transcript,
        Rect::new(10, 0, 1, 10),
        100,
        10,
        90,
    );
    mouse::route(
        &mut app,
        event(MouseEventKind::Down(MouseButton::Left), 10, 9),
    );
    mouse::route(
        &mut app,
        event(MouseEventKind::Drag(MouseButton::Left), 10, 8),
    );

    assert_eq!(app.view.scroll, 10);
    assert_eq!(app.view.scroll_target, 10);

    mouse::route(
        &mut app,
        event(MouseEventKind::Up(MouseButton::Left), 10, 8),
    );
    assert!(!app.view.mouse.is_dragging_scrollbar());
}

#[test]
fn topmost_registered_scrollbar_owns_the_drag() {
    let mut app = App::default();
    let area = Rect::new(0, 0, 11, 10);
    let bar = Rect::new(10, 0, 1, 10);
    mouse::register_region(&mut app, area, mouse::MouseTarget::Transcript);
    mouse::register_scrollbar(&mut app, mouse::MouseTarget::Transcript, bar, 100, 10, 90);
    mouse::register_region(&mut app, area, mouse::MouseTarget::Completion);
    mouse::register_scrollbar(&mut app, mouse::MouseTarget::Completion, bar, 100, 10, 90);

    mouse::route(
        &mut app,
        event(MouseEventKind::Down(MouseButton::Left), 10, 9),
    );
    mouse::route(
        &mut app,
        event(MouseEventKind::Drag(MouseButton::Left), 10, 8),
    );

    assert_eq!(app.completion.scroll, 80);
    assert_eq!(app.view.scroll, 0);
}

fn event(kind: MouseEventKind, column: u16, row: u16) -> MouseEvent {
    MouseEvent {
        kind,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    }
}
