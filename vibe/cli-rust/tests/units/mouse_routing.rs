//! Paint-order hit testing and press-to-release ownership.

use crossterm::event::{MouseButton, MouseEventKind};
use ratatui::layout::Rect;

use vibe_rs::app::App;
use vibe_rs::chat_input;
use vibe_rs::mouse::{self, MouseTarget};
use vibe_rs::selection::{Granularity, Region};
use vibe_rs::vibe_code_project::{self as project, Create, Field};

use crate::support::{down, drag, mouse, stub, up};

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

    assert_eq!(mouse::route(&mut app, down(5, 7)), Some(MouseTarget::Mcp));
    assert_eq!(mouse::route(&mut app, drag(5, 4)), Some(MouseTarget::Mcp));
    assert_eq!(mouse::route(&mut app, up(5, 4)), Some(MouseTarget::Mcp));
    assert_eq!(mouse::route(&mut app, up(5, 4)), None);
}

#[test]
fn resume_picker_ignores_wheel_while_loading() {
    let mut app = App::default();
    mouse::register_region(&mut app, Rect::new(0, 0, 20, 10), MouseTarget::ResumePicker);

    assert_eq!(
        mouse::route(&mut app, mouse(MouseEventKind::ScrollDown, 5, 5)),
        None
    );
    assert_eq!(app.resume_picker.list.scroll(), 0);
    assert!(!app.resume_picker.list.free_scroll());
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
fn scrollbar_track_does_not_fall_through_to_its_content() {
    let mut app = App::default();
    let area = Rect::new(0, 0, 11, 10);
    mouse::register_region(&mut app, area, MouseTarget::RemoteProject);
    mouse::register_scrollbar(
        &mut app,
        MouseTarget::RemoteProject,
        Rect::new(10, 0, 1, 10),
        100,
        10,
        0,
    );

    assert_eq!(mouse::route(&mut app, down(10, 9)), None);
    assert!(!app.view.mouse.is_dragging_scrollbar());
}

#[test]
fn starting_a_new_gesture_clears_remote_project_press_state() {
    let mut app = App::default();
    let area = Rect::new(0, 0, 20, 10);
    mouse::register_region(&mut app, area, MouseTarget::RemoteProject);
    app.vibe_code_project.list.layout_rows(10, area);
    assert_eq!(
        mouse::route(&mut app, down(5, 5)),
        Some(MouseTarget::RemoteProject)
    );
    app.vibe_code_project.list.press((5, 5));

    assert_eq!(
        mouse::route(&mut app, down(6, 6)),
        Some(MouseTarget::RemoteProject)
    );
    assert_eq!(app.vibe_code_project.list.release((5, 5)), None);
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

    assert_eq!(mouse::route(&mut app, down(30, 0)), None);
    assert!(app.view.mouse.is_dragging_scrollbar());
}

#[test]
fn remote_project_fields_extend_selection_during_a_drag() {
    let mut app = App::default();
    app.vibe_code_project.open = true;
    app.vibe_code_project.search_area = Rect::new(10, 5, 20, 1);
    app.vibe_code_project.create = Some(Create {
        name: Field::new("project".into()),
        branch: Field::new("main".into()),
        branch_focused: false,
    });
    let client = stub();

    project::input::mouse(&mut app, &client, down(10, 5));
    project::input::mouse(&mut app, &client, drag(13, 5));
    project::input::mouse(&mut app, &client, up(13, 5));

    let name = &app.vibe_code_project.create.as_ref().unwrap().name;
    assert_eq!(
        chat_input::selected_text(&name.text, name.cursor, name.anchor).as_deref(),
        Some("pro")
    );
}

#[test]
fn remote_project_reuses_double_and_triple_click_selection() {
    let mut app = App::default();
    app.vibe_code_project.open = true;
    app.view.bottom_app_selection_region = Region {
        area: Rect::new(2, 2, 20, 4),
        top: 2,
        ..Region::default()
    };
    mouse::register_region(&mut app, Rect::new(2, 2, 20, 4), MouseTarget::RemoteProject);
    let client = stub();

    for granularity in [Granularity::Char, Granularity::Word, Granularity::Paragraph] {
        for kind in [
            MouseEventKind::Down(MouseButton::Left),
            MouseEventKind::Up(MouseButton::Left),
        ] {
            let event = mouse(kind, 5, 3);
            assert_eq!(
                mouse::route(&mut app, event),
                Some(MouseTarget::RemoteProject)
            );
            project::input::mouse(&mut app, &client, event);
        }
        assert!(app.selection.granularity == granularity);
    }
    assert!(app
        .selection
        .region
        .as_ref()
        .is_some_and(|selection| selection.pending_copy));
}

#[test]
fn the_auth_app_owns_the_padding_beside_its_options() {
    let mut app = App::default();
    app.mcp_oauth.open = true;
    app.mcp_oauth.auth_url = Some("https://auth".into());
    let backend = ratatui::backend::TestBackend::new(80, 24);
    let mut terminal = ratatui::Terminal::new(backend).expect("terminal");
    terminal.draw(|frame| app.draw(frame)).expect("draw");

    let row = app.mcp_oauth.list.rows_of(0).next().unwrap();
    assert_eq!(
        mouse::target_at(&app, (row.x - 1, row.y)),
        Some(MouseTarget::McpOAuth)
    );
}
