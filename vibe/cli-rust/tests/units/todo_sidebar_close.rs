//! Esc undocks the plan panel only while a frame actually showed it.

use vibe_rs::todo_tracker::TodoSidebar;

fn sidebar(open: bool, visible: bool) -> TodoSidebar {
    TodoSidebar {
        open,
        visible,
        ..TodoSidebar::default()
    }
}

#[test]
fn a_docked_panel_takes_the_escape() {
    let mut panel = sidebar(true, true);

    assert!(panel.close());
    assert!(!panel.open);
}

#[test]
fn a_second_escape_before_the_next_frame_passes_through() {
    let mut panel = sidebar(true, true);
    panel.close();

    assert!(!panel.close());
}

#[test]
fn a_dropped_column_keeps_the_panel_open_and_passes_the_escape() {
    let mut panel = sidebar(true, false);

    assert!(!panel.close());
    assert!(panel.open);
}
