//! Subagent list mouse behavior: the hovered row keeps its bar on press, and an overflowing list scrolls by its scrollbar.

use crossterm::event::{MouseButton, MouseEventKind};
use ratatui::layout::Rect;
use vibe_rs::app::App;
use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::subagents;
use vibe_rs::ui::theme;

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
    subagents::focus_first(&mut app);
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
    redraw(&mut app);
    let child = row_of(&app, 1);
    let released = redraw(&mut app);
    assert_eq!(
        released[(child.x, child.y)].bg,
        bar,
        "the viewed child keeps the bar"
    );
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
    subagents::focus_first(&mut app);
    let buffer = redraw(&mut app);
    let area = app.subagents.list.area;
    let x = area.right() - 1;
    let thumb = (area.y..area.bottom())
        .find(|&y| buffer[(x, y)].fg == theme::scrollbar())
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
fn the_list_shows_its_waiting_child_only_while_it_has_focus() {
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
        row(&buffer, area.y + 1).trim_matches(['│', ' ']).to_owned()
    };
    redraw(&mut app);
    assert_eq!(vibe_rs::ui::subagent_list::height(&app), 0);

    assert!(subagents::focus_first(&mut app));
    assert!(last_row(&mut app).contains("[ready]"));

    subagents::leave_list(&mut app);
    assert_eq!(vibe_rs::ui::subagent_list::height(&app), 0);
}

#[test]
fn a_hovered_list_keeps_its_order_until_the_mouse_leaves() {
    let mut app = App::default();
    let set = |app: &mut App, id: &str, status| {
        app.subagents.replace_child_session(PublicChildSession {
            id: id.into(),
            status,
            ..Default::default()
        });
        subagents::refresh(app);
    };
    for id in ["a", "b", "c"] {
        set(&mut app, id, SessionStatus::Running);
    }
    subagents::show_subagent_chat(&mut app, "c");
    subagents::focus_input(&mut app);
    redraw(&mut app);
    let a = app.subagents.list.options.rows_of(1).next().unwrap();
    send(&mut app, MouseEventKind::Moved, a);
    set(&mut app, "a", SessionStatus::Idle);
    let ids = |app: &App| {
        app.subagents
            .list
            .rows
            .iter()
            .map(|r| r.id.clone())
            .collect::<Vec<_>>()
    };
    assert_eq!(
        ids(&app),
        ["a", "b", "c"],
        "rows hold still under the mouse"
    );

    send_mouse(&mut app, mouse(MouseEventKind::Moved, 0, 0));
    assert_eq!(ids(&app), ["b", "c", "a"]);
}
