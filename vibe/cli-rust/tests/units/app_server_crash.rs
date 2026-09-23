//! The app-server crash notice renders and settles the session state.

use ratatui::backend::TestBackend;
use ratatui::Terminal;
use serde_json::json;
use std::sync::Arc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::submission;
use vibe_rs::config;
use vibe_rs::event_loop::notifications::surface_server_close;
use vibe_rs::server::Client;
use vibe_rs::ui;

const WIDTH: u16 = 120;
const HEIGHT: u16 = 30;
const MESSAGE: &str = "App server crashed — the session is no longer active.";

#[test]
fn a_crash_notice_is_visible_in_the_transcript() {
    // A bare notice rendered zero lines before; the viewport then culled it as
    // hidden, so the crash never reached the screen.
    let mut app = App::default();
    app.view.transcript.add(&json!({
        "entry": {"id": "n1", "type": "notice", "level": "error", "message": MESSAGE, "local": true}
    }));
    let mut terminal = Terminal::new(TestBackend::new(WIDTH, HEIGHT)).expect("terminal");
    terminal
        .draw(|frame| ui::draw(&mut app, frame))
        .expect("draw");
    let buffer = terminal.backend().buffer();
    let screen: String = (0..HEIGHT)
        .flat_map(|y| (0..WIDTH).map(move |x| (x, y)))
        .map(|(x, y)| buffer[(x, y)].symbol().to_string())
        .collect();
    assert!(
        screen.contains("App server crashed"),
        "crash notice is not rendered"
    );
}

#[test]
fn a_server_notice_without_a_rendered_kind_mounts_nothing() {
    // Python's `_handle_notice` dispatches by detail kind; notices like
    // `session_title_updated` update state without mounting a widget, so a
    // server notice's message must stay off the transcript.
    let mut app = App::default();
    app.view.transcript.add(&json!({
        "entry": {"id": "n1", "type": "notice", "level": "info", "message": "Session title updated"}
    }));
    let mut terminal = Terminal::new(TestBackend::new(WIDTH, HEIGHT)).expect("terminal");
    terminal
        .draw(|frame| ui::draw(&mut app, frame))
        .expect("draw");
    let buffer = terminal.backend().buffer();
    let screen: String = (0..HEIGHT)
        .flat_map(|y| (0..WIDTH).map(move |x| (x, y)))
        .map(|(x, y)| buffer[(x, y)].symbol().to_string())
        .collect();
    assert!(
        !screen.contains("Session title updated"),
        "server notice rendered a message"
    );
}

#[test]
fn a_crash_locks_the_session() {
    // A generating turn stops: no spinner, no composer, no queue edits.
    let mut app = App::default();
    app.terminal_notifier.configure(true, true);
    app.set_status(Status::Generating {
        since: std::time::Instant::now(),
    });
    assert_eq!(
        app.terminal_notifier.take_title().as_deref(),
        Some(">> Vibe")
    );
    assert!(surface_server_close(&mut app, true));
    assert!(matches!(app.session.status, Status::Failed));
    assert_eq!(app.terminal_notifier.take_title().as_deref(), Some("Vibe"));
    assert!(app.session.startup_error.is_some());
    assert!(app.server_closed);
    assert!(!app.view.transcript.is_empty());

    // A crash during startup keeps the same inert state.
    let mut app = App::default();
    assert!(surface_server_close(&mut app, true));
    assert!(matches!(app.session.status, Status::Failed));

    // A clean stop closes the state without failing or mounting anything.
    let mut app = App::default();
    app.session.status = Status::Generating {
        since: std::time::Instant::now(),
    };
    assert!(!surface_server_close(&mut app, false));
    assert!(matches!(app.session.status, Status::Generating { .. }));
    assert!(app.view.transcript.is_empty());
}

#[test]
fn a_submit_after_a_crash_goes_nowhere() {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = tokio::sync::mpsc::channel::<config::Loaded>(1);
    let mut app = App::default();
    assert!(surface_server_close(&mut app, true));
    app.chat_input.input = "hello?".into();

    submission::submit(&mut app, &client, &config_tx);

    // Nothing was sent to the dead server and the draft stays untouched.
    assert_eq!(app.chat_input.input, "hello?");
    assert!(app.queue.is_empty());
    assert!(app.pending_commands.is_empty());
    assert!(matches!(app.session.status, Status::Failed));
}
