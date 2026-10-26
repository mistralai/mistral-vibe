//! A plain click on the footer's model opens `/model`; busy turns, drags and double clicks do not.

use std::sync::Arc;
use std::time::Instant;

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::Terminal;
use tokio::sync::mpsc;

use vibe_rs::app::{App, Status};
use vibe_rs::mouse;
use vibe_rs::pointer::{self, Shape};
use vibe_rs::server::Client;
use vibe_rs::telemetry::{self, event, TelemetryEvent};
use vibe_rs::ui::bottom_bar;

const WIDTH: u16 = 120;
const MODEL: &str = "mistral-medium-3.5[medium]";

/// The footer of a ready session painted at row 0, and the column its model name starts at.
fn footer() -> (App, u16) {
    let mut app = App::default();
    app.set_status(Status::Ready);
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (0, 600_000);
    app.session.startup_config.active_model = MODEL.into();
    let start = redraw(&mut app).find(MODEL).expect("model") as u16;
    (app, start)
}

fn paint(app: &mut App) -> Buffer {
    let mut terminal = Terminal::new(TestBackend::new(WIDTH, 1)).expect("terminal");
    terminal
        .draw(|frame| bottom_bar::draw(app, frame, Rect::new(0, 0, WIDTH, 1)))
        .expect("draw");
    terminal.backend().buffer().clone()
}

fn redraw(app: &mut App) -> String {
    let buffer = paint(app);
    (0..WIDTH).map(|x| buffer[(x, 0)].symbol()).collect()
}

/// Hovering the model shows no link style or hand, and a click leaves `/model` closed.
fn assert_inert(app: &mut App, start: u16) {
    hover(app, start + 2);
    let buffer = paint(app);
    assert!(!bottom_bar::model_at(app, (start + 2, 0)));
    assert_eq!(pointer::shape(app), Shape::Default);
    assert_eq!(buffer[(start + 2, 0)].style(), buffer[(2, 0)].style());

    click(app, start + 2);
    assert!(!app.model_picker.open);
}

fn send(app: &mut App, kind: MouseEventKind, column: u16) {
    let client = Arc::new(Client::stub());
    let (config_tx, _rx) = mpsc::channel(1);
    let event = MouseEvent {
        kind,
        column,
        row: 0,
        modifiers: KeyModifiers::NONE,
    };
    mouse::handle(app, &client, &config_tx, event);
}

fn click(app: &mut App, column: u16) {
    send(app, MouseEventKind::Down(MouseButton::Left), column);
    send(app, MouseEventKind::Up(MouseButton::Left), column);
}

fn hover(app: &mut App, column: u16) {
    send(app, MouseEventKind::Moved, column);
}

#[test]
fn a_plain_click_on_the_model_opens_the_picker_and_records_the_footer_entry() {
    let (mut app, start) = footer();
    let (tx, mut rx) = mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(tx);

    click(&mut app, start + 2);

    assert!(app.model_picker.open);
    let events: Vec<TelemetryEvent> = std::iter::from_fn(|| rx.try_recv().ok()).collect();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].name, event::SLASH_COMMAND_USED);
    assert_eq!(events[0].properties["command"], "model");
    assert_eq!(events[0].properties["command_type"], "builtin");
    assert_eq!(events[0].properties["entry_point"], "footer");
}

#[test]
fn the_model_is_neither_clickable_nor_hovered_while_a_turn_runs() {
    let (mut app, start) = footer();
    app.set_status(Status::Generating {
        since: Instant::now(),
    });

    assert_inert(&mut app, start);
}

#[test]
fn the_model_is_neither_clickable_nor_hovered_while_the_session_starts() {
    let (mut app, start) = footer();
    app.set_status(Status::Starting);

    assert_inert(&mut app, start);
}

#[test]
fn the_model_is_neither_clickable_nor_hovered_once_the_session_failed() {
    let (mut app, start) = footer();
    app.set_status(Status::Failed);

    assert_inert(&mut app, start);
}

#[test]
fn the_model_is_neither_clickable_nor_hovered_while_a_shell_command_runs() {
    let (mut app, start) = footer();
    app.session.shell_operation_id = Some("shell-1".into());

    assert_inert(&mut app, start);
}

#[test]
fn the_model_shows_the_link_style_and_pointer_hand_once_ready() {
    let (mut app, start) = footer();

    hover(&mut app, start + 2);
    let buffer = paint(&mut app);

    assert_eq!(pointer::shape(&app), Shape::Pointer);
    assert_ne!(buffer[(start + 2, 0)].style(), buffer[(2, 0)].style());
}

#[test]
fn a_drag_back_to_its_start_cell_does_not_open_the_picker() {
    let (mut app, start) = footer();

    send(&mut app, MouseEventKind::Down(MouseButton::Left), start + 2);
    send(&mut app, MouseEventKind::Drag(MouseButton::Left), start + 6);
    send(&mut app, MouseEventKind::Drag(MouseButton::Left), start + 2);
    send(&mut app, MouseEventKind::Up(MouseButton::Left), start + 2);

    assert!(!app.model_picker.open);
}

#[test]
fn a_release_off_the_model_does_not_open_the_picker() {
    let (mut app, start) = footer();

    send(&mut app, MouseEventKind::Down(MouseButton::Left), start + 2);
    send(&mut app, MouseEventKind::Up(MouseButton::Left), 2);

    assert!(!app.model_picker.open);
}

#[test]
fn a_double_click_on_the_model_opens_the_picker_without_selecting_a_word() {
    let (mut app, start) = footer();

    click(&mut app, start + 2);
    redraw(&mut app);
    click(&mut app, start + 2);

    assert!(app.model_picker.open);
    assert!(
        app.selection.bottom_bar.is_none(),
        "no word is armed to copy"
    );
}
