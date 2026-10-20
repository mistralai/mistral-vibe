//! Pressing a hovered subagent row keeps the bar on it instead of flashing the previous highlight.

use std::sync::Arc;

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::Terminal;
use vibe_rs::app::App;
use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::{mouse, subagents};

fn redraw(app: &mut App) -> Buffer {
    let mut terminal = Terminal::new(TestBackend::new(80, 24)).expect("terminal");
    terminal.draw(|frame| app.draw(frame)).expect("draw");
    terminal.backend().buffer().clone()
}

fn send(app: &mut App, kind: MouseEventKind, row: Rect) {
    let client = Arc::new(vibe_rs::server::Client::stub());
    let (config_tx, _rx) = tokio::sync::mpsc::channel(1);
    let event = MouseEvent {
        kind,
        column: row.x + 4,
        row: row.y,
        modifiers: KeyModifiers::NONE,
    };
    mouse::handle(app, &client, &config_tx, event);
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
