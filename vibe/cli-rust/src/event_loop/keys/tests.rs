use std::sync::Arc;

use crossterm::event::{
    Event, KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use ratatui::layout::Rect;
use tokio::sync::mpsc;

use super::{burst_next, handle_input_batch, handle_input_event, next_input, LoopState};
use crate::app::{App, Severity};
use crate::mouse::{pixels, MouseTarget};
use crate::server::Client;
use crate::ui::notice::Notice;

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

/// A transcript drag still held, with pixel reports unsupported so no hold switches the terminal.
fn dragged_message() -> (App, Arc<Client>, mpsc::Sender<crate::config::Loaded>) {
    const WORDS: &str = "Slash commands";
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel(1);
    let mut app = App::default();
    app.view.transcript.add(&serde_json::json!({"entry": {
        "id": "u0",
        "type": "message",
        "role": "user",
        "content": [{"type": "text", "text": WORDS}],
        "generationStatus": "completed",
    }}));
    let mut terminal = ratatui::Terminal::new(ratatui::backend::TestBackend::new(80, 24)).unwrap();
    terminal.draw(|frame| app.draw(frame)).unwrap();
    let buffer = terminal.backend().buffer();
    let (x, y) = (0..buffer.area.height)
        .find_map(|y| {
            let line: String = (0..buffer.area.width)
                .map(|x| buffer[(x, y)].symbol())
                .collect();
            line.find(WORDS)
                .map(|at| (line[..at].chars().count() as u16, y))
        })
        .expect("painted message");
    for (kind, column) in [
        (MouseEventKind::Down(MouseButton::Left), x),
        (MouseEventKind::Drag(MouseButton::Left), x + 4),
    ] {
        let event = MouseEvent {
            kind,
            column,
            row: y,
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
    let (mut app, client, config_tx) = dragged_message();

    handle_input_event(&mut app, &client, &config_tx, Some(Event::FocusLost));

    assert!(copy_armed(&app));
}

#[test]
fn a_key_mid_drag_arms_the_selection_copy() {
    let _guard = pixels::tests::reset(false);
    let (mut app, client, config_tx) = dragged_message();

    handle_input_event(&mut app, &client, &config_tx, Some(key('x')));

    assert!(copy_armed(&app));
}

#[test]
fn esc_mid_drag_aborts_the_selection_without_copying() {
    let _guard = pixels::tests::reset(false);
    let (mut app, client, config_tx) = dragged_message();
    let esc = Event::Key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));

    handle_input_event(&mut app, &client, &config_tx, Some(esc));

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

#[test]
fn keys_and_clicks_the_composer_misses_cancel_a_vim_operator() {
    let _guard = pixels::tests::reset(false);
    let (config_tx, _config_rx) = mpsc::channel(1);
    let client = Arc::new(Client::stub());
    let mut app = App {
        vim: crate::vim::Vim {
            enabled: true,
            mode: crate::vim::Mode::Normal,
            ..crate::vim::Vim::default()
        },
        ..App::default()
    };
    app.chat_input.input = "hello world".into();
    let shift_tab = Event::Key(KeyEvent::new(KeyCode::BackTab, KeyModifiers::SHIFT));
    let click = mouse(MouseEventKind::Down(MouseButton::Left), 0);
    for event in [shift_tab, click, Event::Paste("X".into())] {
        handle_input_event(&mut app, &client, &config_tx, Some(key('d')));
        assert_eq!(app.vim.pending, Some('d'));
        handle_input_event(&mut app, &client, &config_tx, Some(event));
        assert_eq!(app.vim.pending, None);
    }
    handle_input_event(&mut app, &client, &config_tx, Some(key('d')));
    let moved = mouse(MouseEventKind::Moved, 0);
    handle_input_event(&mut app, &client, &config_tx, Some(moved));
    assert_eq!(app.vim.pending, Some('d'));
    // A digit rating feedback reaches the composer but bypasses vim.
    app.feedback.message = crate::feedback::Message::Prompt;
    handle_input_event(&mut app, &client, &config_tx, Some(key('0')));
    assert_eq!(app.vim.pending, None);
}

#[test]
fn a_normal_mode_paste_leaves_the_caret_on_the_last_character() {
    let (config_tx, _config_rx) = mpsc::channel(1);
    let client = Arc::new(Client::stub());
    let mut app = App {
        vim: crate::vim::Vim {
            enabled: true,
            mode: crate::vim::Mode::Normal,
            ..crate::vim::Vim::default()
        },
        ..App::default()
    };
    let paste = Event::Paste("abc".into());
    handle_input_event(&mut app, &client, &config_tx, Some(paste));
    handle_input_event(&mut app, &client, &config_tx, Some(key('x')));
    assert_eq!(app.chat_input.input, "ab");
}

/// A sticky notice of `severity` already painted once, as a key would find it.
fn painted(severity: Severity, text: &str) -> Notice {
    let mut notice = Notice::new(severity, text, None);
    notice.mark_shown();
    notice
}

fn send(app: &mut App, event: Event) {
    let client = Arc::new(Client::stub());
    let (config_tx, _config_rx) = mpsc::channel(1);
    handle_input_event(app, &client, &config_tx, Some(event));
}

#[test]
fn any_key_dismisses_a_painted_refusal_whatever_has_focus() {
    let mut app = App::default();
    app.config_screen.open = true;
    app.overlays.notice = Some(painted(Severity::Error, "Image is too large"));

    send(&mut app, key('j'));

    assert!(app.overlays.notice.is_none());
}

#[test]
fn a_key_before_the_first_paint_leaves_the_refusal_up() {
    let mut app = App::default();
    crate::ui::notice::show_sticky(&mut app, Severity::Error, "Failed to answer approval");
    app.config_screen.open = true;
    app.config_screen.status = Some(Notice::new(Severity::Error, "Failed", None));

    send(&mut app, key('j'));

    assert!(app.overlays.notice.is_some());
    assert!(app.config_screen.status.is_some());
}

#[test]
fn a_key_leaves_a_confirmation_to_its_timer() {
    let mut app = App::default();
    crate::ui::notice::show(&mut app, Severity::Information, "Copied to clipboard", 4);
    app.overlays.notice.as_mut().unwrap().mark_shown();

    send(&mut app, key('x'));

    assert!(app.overlays.notice.is_some());
}

#[test]
fn any_key_clears_the_painted_config_status_line() {
    let mut app = App::default();
    app.config_screen.open = true;
    app.config_screen.status = Some(painted(Severity::Error, "Failed"));

    send(&mut app, key('j'));

    assert!(app.config_screen.status.is_none());
}

#[test]
fn a_paste_also_answers_a_painted_refusal() {
    let mut app = App::default();
    app.config_screen.open = true;
    app.config_screen.status = Some(painted(Severity::Error, "Failed"));
    app.overlays.notice = Some(painted(
        Severity::Warning,
        "No image found on the clipboard.",
    ));

    send(&mut app, Event::Paste("text".into()));

    assert!(app.config_screen.status.is_none());
    assert!(app.overlays.notice.is_none());
}
