//! Ctrl+C / Ctrl+D double-press quit confirmation and its bottom-bar hint.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::Terminal;
use vibe_rs::app::App;
use vibe_rs::input::handle_priority_key;
use vibe_rs::message_queue::QueueItem;
use vibe_rs::quit_manager::QuitConfirmKey;
use vibe_rs::server::Client;
use vibe_rs::ui::bottom_bar;

fn press(app: &mut App, key: char) -> Option<bool> {
    let event = KeyEvent::new(KeyCode::Char(key), KeyModifiers::CONTROL);
    handle_priority_key(app, &Arc::new(Client::stub()), event)
}

#[test]
fn only_the_arming_key_confirms_quit() {
    let mut app = App::default();
    assert_eq!(press(&mut app, 'c'), Some(false));
    assert_eq!(press(&mut app, 'd'), Some(false));
    assert!(app.quit.is_confirmed(QuitConfirmKey::CtrlD));
    assert_eq!(press(&mut app, 'd'), Some(true));
}

#[test]
fn ctrl_d_with_queued_prompts_warns_they_will_be_discarded() {
    let mut app = App::default();
    for id in ["q1", "q2"] {
        app.queue.items.push(QueueItem {
            queue_item_id: Some(id.into()),
            message_id: id.into(),
            server_message_id: id.into(),
            text: "queued".into(),
            images: Vec::new(),
            mentions: None,
            sent: true,
            ever_sent: true,
            revision: 1,
            replacing: false,
        });
    }
    assert_eq!(press(&mut app, 'd'), Some(false));
    assert_eq!(app.queue.len(), 2, "arming the quit keeps the queue");
    let width = 120;
    let mut terminal = Terminal::new(TestBackend::new(width, 1)).expect("terminal");
    terminal
        .draw(|frame| bottom_bar::draw(&mut app, frame, Rect::new(0, 0, width, 1)))
        .expect("draw");
    let buffer = terminal.backend().buffer();
    let row: String = (0..width).map(|x| buffer[(x, 0)].symbol()).collect();
    assert!(row.starts_with("Press Ctrl+D again to quit (2 queued messages will be discarded)"));
}

#[test]
fn ctrl_d_quits_at_once_when_confirmation_is_off_and_keeps_the_hidden_draft() {
    let mut app = App::default();
    app.quit.ask_confirmation_on_exit = false;
    app.subagents.viewed_subagent_id = Some("child".into());
    app.chat_input.input = "draft".into();
    app.chat_input.cursor = 0;
    assert_eq!(press(&mut app, 'd'), Some(true));
    assert_eq!(app.chat_input.input, "draft");
}
