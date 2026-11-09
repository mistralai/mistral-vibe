//! A hovered subagent row stays readable on ansi-dark: the hover tint must not blank its bar.

use std::sync::Arc;

use crossterm::event::{KeyModifiers, MouseEvent, MouseEventKind};
use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::Terminal;
use vibe_rs::app::App;
use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::ui::theme;
use vibe_rs::{mouse, subagents};

fn redraw(app: &mut App) -> Buffer {
    let mut terminal = Terminal::new(TestBackend::new(80, 24)).expect("terminal");
    terminal.draw(|frame| app.draw(frame)).expect("draw");
    terminal.backend().buffer().clone()
}

#[test]
fn a_hovered_subagent_row_keeps_its_text_visible() {
    theme::prepare_active("ansi-dark", "ansi-dark");
    let mut app = App::default();
    app.subagents.replace_child_session(PublicChildSession {
        id: "a".into(),
        name: "a-name".into(),
        agent_type: "explore".into(),
        status: SessionStatus::Running,
        ..Default::default()
    });
    subagents::refresh(&mut app);
    redraw(&mut app);
    let row = app
        .subagents
        .list
        .options
        .rows_of(1)
        .next()
        .expect("child row");
    let client = Arc::new(vibe_rs::server::Client::stub());
    let (config_tx, _rx) = tokio::sync::mpsc::channel(1);
    mouse::handle(
        &mut app,
        &client,
        &config_tx,
        MouseEvent {
            kind: MouseEventKind::Moved,
            column: row.x + 4,
            row: row.y,
            modifiers: KeyModifiers::NONE,
        },
    );
    assert_eq!(app.subagents.list.mouse_session_id.as_deref(), Some("a"));

    let buffer = redraw(&mut app);
    for x in row.x + 1..row.right() - 1 {
        let cell = &buffer[(x, row.y)];
        if cell.symbol() != " " {
            assert_ne!(cell.fg, cell.bg, "cell ({x}, {}) is unreadable", row.y);
        }
    }
}
