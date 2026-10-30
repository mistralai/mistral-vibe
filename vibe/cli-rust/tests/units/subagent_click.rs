//! Subagent list mouse behavior: the hovered row keeps its bar on press, and an overflowing list scrolls by its scrollbar.

use crossterm::event::{MouseButton, MouseEventKind};
use ratatui::layout::Rect;
use vibe_rs::app::App;
use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::subagents;

use crate::support::{down, drag, mouse, redraw, row, send_mouse, up};

fn send(app: &mut App, kind: MouseEventKind, row: Rect) {
    send_mouse(app, mouse(kind, row.x + 4, row.y));
}

#[test]
fn pressing_the_hovered_row_keeps_the_bar_on_it() {
    let mut app = App::default();
    app.subagents.replace_child_session(PublicChildSession {
        id: "a".into(),
        status: SessionStatus::Running,
        ..Default::default()
    });
    subagents::refresh(&mut app);
    redraw(&mut app);
    let row_of = |app: &App, index| app.subagents.list.options.rows_of(index).next().unwrap();
    let (main, child) = (row_of(&app, 0), row_of(&app, 1));
    let base = redraw(&mut app)[(child.x, child.y)].bg;

    send(&mut app, MouseEventKind::Moved, child);
    let bar = redraw(&mut app)[(child.x, child.y)].bg;
    assert_ne!(bar, base);

    send(&mut app, MouseEventKind::Down(MouseButton::Left), child);
    let pressed = redraw(&mut app);
    assert_eq!(pressed[(child.x, child.y)].bg, bar);
    assert_ne!(pressed[(main.x, main.y)].bg, bar);

    send(&mut app, MouseEventKind::Up(MouseButton::Left), child);
    let released = redraw(&mut app);
    assert_eq!(released[(child.x, child.y)].bg, bar);
    assert_eq!(app.subagents.viewed_subagent_id.as_deref(), Some("a"));
}

#[test]
fn an_overflowing_list_shows_a_draggable_scrollbar() {
    let mut app = App::default();
    for id in ["a", "b", "c", "d", "e", "f", "g", "h"] {
        app.subagents.replace_child_session(PublicChildSession {
            id: id.into(),
            status: SessionStatus::Running,
            ..Default::default()
        });
    }
    subagents::refresh(&mut app);
    let buffer = redraw(&mut app);
    let area = app.subagents.list.area;
    let x = area.right() - 1;
    let thumb = (area.y..area.bottom())
        .find(|&y| buffer[(x, y)].symbol() != " ")
        .expect("a scrollbar thumb in the last column");
    assert_eq!(app.subagents.list.options.scroll(), 0);

    let bottom = area.bottom() - 1;
    vibe_rs::mouse::route(&mut app, down(x, thumb));
    vibe_rs::mouse::route(&mut app, drag(x, bottom));
    vibe_rs::mouse::route(&mut app, up(x, bottom));
    assert!(
        app.subagents.list.options.scroll() > 0,
        "dragging scrolls the list"
    );
}

#[test]
fn a_list_that_fits_has_no_scrollbar() {
    let mut app = App::default();
    app.subagents.replace_child_session(PublicChildSession {
        id: "a".into(),
        status: SessionStatus::Running,
        ..Default::default()
    });
    subagents::refresh(&mut app);
    redraw(&mut app);
    let area = app.subagents.list.area;
    assert!(vibe_rs::mouse::scrollbar_tracks(&app, area).is_empty());
}

#[test]
fn the_waiting_row_offers_a_only_while_the_list_has_focus() {
    let mut app = App::default();
    for status in [SessionStatus::Running, SessionStatus::Idle] {
        app.subagents.replace_child_session(PublicChildSession {
            id: "a".into(),
            status,
            ..Default::default()
        });
        subagents::refresh(&mut app);
    }
    let last_row = |app: &mut App| {
        let buffer = redraw(app);
        let area = app.subagents.list.area;
        row(&buffer, area.bottom() - 1)
    };
    assert_eq!(last_row(&mut app).trim(), "1 subagent waiting");

    assert!(subagents::focus_first(&mut app));
    assert_eq!(last_row(&mut app).trim(), "1 subagent waiting · a to show");

    subagents::leave_list(&mut app);
    assert_eq!(last_row(&mut app).trim(), "1 subagent waiting");
}

#[test]
fn an_overflowing_list_keeps_its_waiting_row_below_the_scrollbar() {
    let mut app = App::default();
    for id in ["a", "b", "c", "d", "e", "f", "g", "w"] {
        app.subagents.replace_child_session(PublicChildSession {
            id: id.into(),
            status: SessionStatus::Running,
            ..Default::default()
        });
        subagents::refresh(&mut app);
    }
    app.subagents.replace_child_session(PublicChildSession {
        id: "w".into(),
        status: SessionStatus::Idle,
        ..Default::default()
    });
    app.subagents.list.batch_session_ids.remove("w");
    subagents::refresh(&mut app);
    assert_eq!(app.subagents.list.waiting_count, 1);

    let buffer = redraw(&mut app);
    let area = app.subagents.list.area;
    assert_eq!(area.height, 7, "6 list rows and the waiting row");
    assert_eq!(row(&buffer, area.bottom() - 1).trim(), "1 subagent waiting");
    let tracks = vibe_rs::mouse::scrollbar_tracks(&app, area);
    assert_eq!(tracks.len(), 1);
    assert_eq!(tracks[0].height, 6, "the bar spans only the list rows");
    assert!(tracks[0].bottom() < area.bottom());
}
