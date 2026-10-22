use std::sync::Arc;

use crossterm::event::{
    Event, KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use ratatui::layout::Rect;
use tokio::sync::mpsc;

use super::{burst_next, handle_input_batch, handle_input_event, next_input, LoopState};
use crate::app::{App, ToastSeverity};
use crate::mouse::{pixels, MouseTarget};
use crate::server::Client;

fn mouse(kind: MouseEventKind, row: u16) -> Event {
    Event::Mouse(MouseEvent {
        kind,
        column: 1,
        row,
        modifiers: KeyModifiers::NONE,
    })
}

fn key(c: char) -> Event {
    Event::Key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE))
}

fn queue(events: Vec<Event>) -> mpsc::Receiver<Event> {
    let (tx, rx) = mpsc::channel(16);
    for event in events {
        tx.try_send(event).unwrap();
    }
    rx
}

#[test]
fn a_burst_takes_queued_motion_and_wheel_reports() {
    let drag = MouseEventKind::Drag(MouseButton::Left);
    let mut input = queue(vec![
        mouse(drag, 1),
        mouse(MouseEventKind::ScrollDown, 2),
        mouse(MouseEventKind::Moved, 3),
    ]);
    let mut deferred = None;

    assert_eq!(burst_next(&mut input, &mut deferred), Some(mouse(drag, 1)));
    assert_eq!(
        burst_next(&mut input, &mut deferred),
        Some(mouse(MouseEventKind::ScrollDown, 2))
    );
    assert_eq!(
        burst_next(&mut input, &mut deferred),
        Some(mouse(MouseEventKind::Moved, 3))
    );
    assert_eq!(burst_next(&mut input, &mut deferred), None);
    assert_eq!(deferred, None);
}

#[test]
fn a_burst_defers_the_first_other_input_and_leaves_the_rest_queued() {
    let release = mouse(MouseEventKind::Up(MouseButton::Left), 4);
    let mut input = queue(vec![release.clone(), key('a')]);
    let mut deferred = None;

    assert_eq!(burst_next(&mut input, &mut deferred), None);
    assert_eq!(deferred, Some(release));
    assert_eq!(input.try_recv().ok(), Some(key('a')));
}

#[test]
fn deferred_input_is_served_before_newer_input() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .build()
        .unwrap();
    let mut input = queue(vec![key('b')]);
    let mut deferred = Some(key('a'));

    runtime.block_on(async {
        assert_eq!(next_input(&mut input, &mut deferred).await, Some(key('a')));
        assert_eq!(next_input(&mut input, &mut deferred).await, Some(key('b')));
    });
    assert_eq!(deferred, None);
}

/// A toast drag still held, with pixel reports unsupported so no hold switches the terminal.
fn dragged_toast() -> (App, Arc<Client>, mpsc::Sender<crate::config::Loaded>) {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel(1);
    let mut app = App::default();
    app.show_toast("Slash commands".to_owned(), ToastSeverity::Warning, 60);
    let mut terminal = ratatui::Terminal::new(ratatui::backend::TestBackend::new(80, 24)).unwrap();
    terminal.draw(|frame| app.draw(frame)).unwrap();
    let (_, area) = *app.view.toast_text_areas.last().unwrap();
    for (kind, column) in [
        (MouseEventKind::Down(MouseButton::Left), area.x),
        (MouseEventKind::Drag(MouseButton::Left), area.x + 4),
    ] {
        let event = MouseEvent {
            kind,
            column,
            row: area.y,
            modifiers: KeyModifiers::NONE,
        };
        crate::mouse::handle(&mut app, &client, &config_tx, event);
    }
    (app, client, config_tx)
}

fn copy_armed(app: &App) -> bool {
    app.selection
        .region
        .as_ref()
        .is_some_and(|selection| selection.pending_copy)
}

#[test]
fn losing_focus_mid_drag_arms_the_selection_copy() {
    let _guard = pixels::tests::reset(false);
    let (mut app, client, config_tx) = dragged_toast();

    handle_input_event(&mut app, &client, &config_tx, Some(Event::FocusLost), false);

    assert!(copy_armed(&app));
}

#[test]
fn a_key_mid_drag_arms_the_selection_copy() {
    let _guard = pixels::tests::reset(false);
    let (mut app, client, config_tx) = dragged_toast();

    handle_input_event(&mut app, &client, &config_tx, Some(key('x')), false);

    assert!(copy_armed(&app));
}

#[test]
fn esc_mid_drag_aborts_the_selection_without_copying() {
    let _guard = pixels::tests::reset(false);
    let (mut app, client, config_tx) = dragged_toast();
    let esc = Event::Key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));

    handle_input_event(&mut app, &client, &config_tx, Some(esc), false);

    assert!(!copy_armed(&app));
    assert!(!app.view.mouse.holds_target());
}

#[test]
fn a_gesture_holds_cell_reports_from_its_press_to_its_end() {
    let _guard = pixels::tests::reset(true);
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel(1);
    let mut app = App::default();
    crate::mouse::register_region(&mut app, Rect::new(0, 0, 100, 40), MouseTarget::Transcript);
    let mut input = queue(Vec::new());
    let mut state = LoopState::new();
    let mut feed = |app: &mut App, kind, column, row| {
        let event = Event::Mouse(MouseEvent {
            kind,
            column,
            row,
            modifiers: KeyModifiers::NONE,
        });
        handle_input_batch(
            app,
            &client,
            &config_tx,
            &mut input,
            Some(event),
            false,
            &mut state,
        );
        pixels::grid().is_some()
    };
    let (down, drag, up) = (
        MouseEventKind::Down(MouseButton::Left),
        MouseEventKind::Drag(MouseButton::Left),
        MouseEventKind::Up(MouseButton::Left),
    );

    // The press arrives in pixels (cell 5,5); the rest of the gesture in cells.
    assert!(!feed(&mut app, down, 54, 104));
    assert!(!feed(&mut app, drag, 6, 5));
    assert!(feed(&mut app, up, 6, 5));
    // A lost release: motion without a button ends the gesture and its hold.
    assert!(!feed(&mut app, down, 54, 104));
    assert!(feed(&mut app, MouseEventKind::Moved, 6, 5));
}
