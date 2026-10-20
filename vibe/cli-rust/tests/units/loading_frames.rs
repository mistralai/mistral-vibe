//! Frames painted before the session is ready or while the saved sessions load.

use std::sync::Arc;

use ratatui::backend::TestBackend;
use ratatui::Terminal;
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::submission;
use vibe_rs::config;
use vibe_rs::event_handler::apply_startup_event;
use vibe_rs::resume_picker::Session;
use vibe_rs::server::Client;
use vibe_rs::startup::StartupEvent;
use vibe_rs::ui;

use crate::resume_picker_outcomes::{picking, SAVED};

const WIDTH: u16 = 120;
const HEIGHT: u16 = 30;

fn screen(app: &mut App) -> Vec<String> {
    let mut terminal = Terminal::new(TestBackend::new(WIDTH, HEIGHT)).unwrap();
    terminal.draw(|frame| ui::draw(app, frame)).unwrap();
    let buffer = terminal.backend().buffer();
    (0..HEIGHT)
        .map(|y| (0..WIDTH).map(|x| buffer[(x, y)].symbol()).collect())
        .collect()
}

#[test]
fn an_open_picker_shows_a_loading_line_until_its_sessions_arrive() {
    let mut app = picking();
    app.resume_picker.sessions.clear();
    assert!(screen(&mut app).join("\n").contains("Loading sessions..."));
    app.resume_picker.sessions = vec![Session {
        id: SAVED.into(),
        title: "Saved implementation".into(),
        ..Session::default()
    }];
    let listed = screen(&mut app).join("\n");
    assert!(!listed.contains("Loading sessions..."));
    assert!(listed.contains("Saved implementation"));
}

#[test]
fn a_failed_session_start_shows_the_error_and_sends_nothing() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel::<config::Loaded>(1);
    let mut app = App::default();
    let failed = StartupEvent::Failed("session/start failed: session start failed".into());
    assert!(!apply_startup_event(&mut app, &client, &config_tx, failed));
    assert!(matches!(app.session.status, Status::Failed));
    let text = screen(&mut app).join("\n");
    assert!(
        text.contains("Failed to initialize: session/start failed: session start failed"),
        "fatal error missing:\n{text}"
    );

    app.chat_input.input = "hi".into();
    submission::submit(&mut app, &client, &config_tx);
    assert!(app.queue.is_empty());
    assert!(app.pending_commands.is_empty());
    assert!(matches!(app.session.status, Status::Failed));
}
