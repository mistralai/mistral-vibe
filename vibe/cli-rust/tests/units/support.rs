//! Shared unit-test helpers: keys, mouse events, App builders, rendering and fixtures.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::{Frame, Terminal};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::message_queue::QueueItem;
use vibe_rs::resume_picker::Session;
use vibe_rs::selection::Region;
use vibe_rs::server::{
    Client, HistoryEntry, MessageContent, Notification, QuestionChoice, UserQuestion,
};
use vibe_rs::transcript::Transcript;

pub fn stub() -> Arc<Client> {
    Arc::new(Client::stub())
}

pub fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

pub fn ctrl(character: char) -> KeyEvent {
    KeyEvent::new(KeyCode::Char(character), KeyModifiers::CONTROL)
}

/// Route one key through the app's top-level input handler; true means exit.
pub fn send_key(app: &mut App, key: KeyEvent) -> bool {
    let (config_tx, _config_rx) = mpsc::channel(1);
    vibe_rs::input::handle_key(app, &stub(), &config_tx, key)
}

pub fn press(app: &mut App, code: KeyCode) -> bool {
    send_key(app, key(code))
}

pub fn type_text(app: &mut App, text: &str) {
    for character in text.chars() {
        press(app, KeyCode::Char(character));
    }
}

pub fn mouse(kind: MouseEventKind, column: u16, row: u16) -> MouseEvent {
    MouseEvent {
        kind,
        column,
        row,
        modifiers: KeyModifiers::NONE,
    }
}

pub fn down(column: u16, row: u16) -> MouseEvent {
    mouse(MouseEventKind::Down(MouseButton::Left), column, row)
}

pub fn up(column: u16, row: u16) -> MouseEvent {
    mouse(MouseEventKind::Up(MouseButton::Left), column, row)
}

pub fn drag(column: u16, row: u16) -> MouseEvent {
    mouse(MouseEventKind::Drag(MouseButton::Left), column, row)
}

/// Route one mouse event through the app's top-level mouse handler.
pub fn send_mouse(app: &mut App, event: MouseEvent) {
    let (config_tx, _config_rx) = mpsc::channel(1);
    vibe_rs::mouse::handle(app, &stub(), &config_tx, event);
}

/// A left press and release at one cell through the top-level mouse handler.
pub fn click(app: &mut App, column: u16, row: u16) {
    send_mouse(app, down(column, row));
    send_mouse(app, up(column, row));
}

pub fn press_question(app: &mut App, code: KeyCode) {
    vibe_rs::question_input::handle_key(app, &stub(), key(code));
}

pub fn question_mouse(app: &mut App, event: MouseEvent) {
    vibe_rs::question_input::handle_mouse(app, &stub(), event);
}

pub fn with_status(status: Status) -> App {
    let mut app = App::default();
    app.session.status = status;
    app
}

pub fn choice(label: &str) -> QuestionChoice {
    QuestionChoice {
        label: label.into(),
        description: String::new(),
    }
}

pub fn question(text: &str, labels: &[&str], multi_select: bool) -> UserQuestion {
    UserQuestion {
        question: text.into(),
        header: String::new(),
        options: labels.iter().map(|label| choice(label)).collect(),
        multi_select,
        hide_other: false,
    }
}

/// An app with the question app open on `questions`.
pub fn with_questions(questions: Vec<UserQuestion>) -> App {
    let mut app = App::default();
    app.question_app.open = true;
    app.question_app.questions = questions;
    app
}

/// The default test question: "Pick" between Alpha and Beta.
pub fn pick(multi_select: bool) -> UserQuestion {
    question("Pick", &["Alpha", "Beta"], multi_select)
}

pub fn with_question(multi_select: bool) -> App {
    with_questions(vec![pick(multi_select)])
}

/// Lay out the question's option rows at screen rows, as the render would.
pub fn set_question_rows(app: &mut App, rows: &[(u16, usize)]) {
    let height = rows.iter().map(|(row, _)| row + 1).max().unwrap_or(0);
    let mut lines = vec![None; usize::from(height)];
    for (row, option) in rows {
        lines[usize::from(*row)] = Some(*option);
    }
    app.question_app
        .list
        .layout(&lines, Rect::new(0, 0, 80, height));
}

/// The box-content region the question draw publishes, as the render would.
pub fn question_region(x: u16, y: u16, width: u16, height: u16) -> Region {
    Region {
        area: Rect::new(x, y, width, height),
        top: i32::from(y),
        ..Region::default()
    }
}

/// An app with the resume picker open on one highlighted saved session, "busy".
pub fn with_picker() -> App {
    let mut app = App::default();
    app.resume_picker.open = true;
    app.resume_picker.sessions = vec![Session {
        id: "busy".into(),
        title: "Saved implementation".into(),
        ..Default::default()
    }];
    app.resume_picker.list.highlight(0);
    app
}

pub fn render(width: u16, height: u16, draw: impl FnOnce(&mut Frame)) -> Buffer {
    let mut terminal = Terminal::new(TestBackend::new(width, height)).expect("terminal");
    terminal.draw(draw).expect("draw");
    terminal.backend().buffer().clone()
}

/// Render the whole UI on an 80x24 terminal the way the event loop does.
pub fn redraw(app: &mut App) -> Buffer {
    render(80, 24, |frame| app.draw(frame))
}

pub fn row(buffer: &Buffer, y: u16) -> String {
    (0..buffer.area.width)
        .map(|x| buffer[(x, y)].symbol())
        .collect()
}

pub fn rows(buffer: &Buffer) -> Vec<String> {
    (0..buffer.area.height).map(|y| row(buffer, y)).collect()
}

/// The bottom bar drawn alone on a one-row terminal of `width` cells.
pub fn bottom_bar(app: &mut App, width: u16) -> Buffer {
    render(width, 1, |frame| {
        vibe_rs::ui::bottom_bar::draw(app, frame, frame.area())
    })
}

pub fn bottom_bar_row(app: &mut App, width: u16) -> String {
    row(&bottom_bar(app, width), 0)
}

/// A prompt the server already queued, with the same UI and server id.
pub fn queued(id: &str, text: &str) -> QueueItem {
    QueueItem {
        queue_item_id: Some(format!("q-{id}")),
        message_id: id.into(),
        server_message_id: id.into(),
        text: text.into(),
        images: Vec::new(),
        mentions: None,
        sent: true,
        ever_sent: true,
        revision: 0,
        replacing: false,
    }
}

pub fn queued_texts(app: &App) -> Vec<&str> {
    app.queue
        .items
        .iter()
        .map(|item| item.text.as_str())
        .collect()
}

/// Apply one server notification and assert the app handled it.
pub fn notify(app: &mut App, method: &str, params: Value) {
    let notification = Notification {
        method: method.into(),
        params,
    };
    assert!(vibe_rs::event_handler::apply_notification(
        app,
        &stub(),
        &notification
    ));
}

/// Yield until spawned stub-client tasks have run.
pub async fn settle() {
    for _ in 0..32 {
        tokio::task::yield_now().await;
    }
}

/// A live transcript built from history entries.
pub fn live(entries: &[Value]) -> Transcript {
    let mut transcript = Transcript::default();
    for entry in entries {
        transcript.add(&json!({ "entry": entry }));
    }
    transcript
}

/// Every transcript message as (role, text, in progress).
pub fn transcript_rows(app: &App) -> Vec<(String, String, bool)> {
    app.view
        .transcript
        .lines()
        .filter_map(|line| match line.entry {
            HistoryEntry::Message(message) => Some((
                message.role.clone(),
                message
                    .content
                    .iter()
                    .filter_map(|block| match block {
                        MessageContent::Text { text } => Some(text.as_str()),
                        _ => None,
                    })
                    .collect(),
                line.entry.in_progress(),
            )),
            _ => None,
        })
        .collect()
}

/// Every transcript message as (role, text).
pub fn messages(app: &App) -> Vec<(String, String)> {
    transcript_rows(app)
        .into_iter()
        .map(|(role, text, _)| (role, text))
        .collect()
}

pub fn session_state(id: &str, history: Vec<Value>) -> vibe_rs::server::PublicSessionState {
    serde_json::from_value(json!({"eventId": 1, "session": {"id": id}, "history": history}))
        .unwrap()
}

/// A completed text message history entry.
pub fn message(id: &str, role: &str, text: &str) -> Value {
    json!({
        "id": id,
        "type": "message",
        "role": role,
        "content": [{"type": "text", "text": text}],
        "generationStatus": "completed",
    })
}
