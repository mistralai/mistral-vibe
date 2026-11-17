//! The `Resuming` spinner and the frozen picker while a picker resume is in flight.

use crossterm::event::KeyCode;
use ratatui::layout::Rect;
use vibe_rs::app::{App, Severity, Status};
use vibe_rs::mouse::{self, MouseTarget};
use vibe_rs::resume_picker::{self, Event, Session};
use vibe_rs::ui;

use crate::resume_picker_outcomes::{open_picker, picking, saved_state, SAVED};
use crate::support::{click, render, rows, stub};

fn screen(app: &mut App) -> String {
    rows(&render(100, 30, |frame| ui::draw(app, frame))).join("\n")
}

fn press(app: &mut App, code: KeyCode) {
    resume_picker::handle_key(app, &stub(), code.into());
}

/// Enter pressed on the saved session, which the picker opened on.
fn resuming() -> App {
    let mut app = picking();
    press(&mut app, KeyCode::Enter);
    assert!(app.resume_picker.resuming);
    app
}

fn generating(app: &mut App) {
    app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });
}

#[tokio::test]
async fn enter_shows_the_resuming_spinner() {
    let mut app = picking();
    assert!(!screen(&mut app).contains("Resuming…"));
    press(&mut app, KeyCode::Enter);
    assert!(screen(&mut app).contains("Resuming…"));
}

#[tokio::test]
async fn a_resume_in_flight_ignores_every_key_but_esc() {
    let mut app = resuming();
    app.resume_picker.sessions.push(Session {
        id: "saved-session-2".into(),
        ..Session::default()
    });
    for code in [KeyCode::Down, KeyCode::Char('j'), KeyCode::Char('d')] {
        press(&mut app, code);
        assert_eq!(app.resume_picker.list.selected(), 0);
        assert!(app.resume_picker.delete_confirm.is_none());
    }
    press(&mut app, KeyCode::Esc);
    assert!(!app.resume_picker.open);
    assert!(!screen(&mut app).contains("Resuming…"));
}

/// A click on the second row, through the central mouse router.
fn click_second_row(app: &mut App) {
    app.resume_picker.sessions.push(Session {
        id: "saved-session-2".into(),
        ..Session::default()
    });
    let area = Rect::new(0, 0, 80, 20);
    mouse::register_region(app, area, MouseTarget::ResumePicker);
    app.resume_picker.list.layout_rows(2, area);
    click(app, 4, 1);
}

#[tokio::test]
async fn a_click_keeps_the_highlight_on_the_session_being_resumed() {
    let mut app = resuming();
    click_second_row(&mut app);
    assert_eq!(app.resume_picker.list.selected(), 0);
}

#[tokio::test]
async fn a_click_keeps_the_highlight_on_the_session_being_deleted() {
    let mut app = picking();
    app.resume_picker.deleting = Some(SAVED.into());
    click_second_row(&mut app);
    assert_eq!(app.resume_picker.list.selected(), 0);
}

#[tokio::test]
async fn every_resume_answer_ends_the_spinner() {
    for answer in [
        Event::Resumed {
            id: SAVED.into(),
            state: saved_state(),
        },
        Event::Error(Severity::Error, "Failed to resume session.".into()),
    ] {
        let mut app = resuming();
        resume_picker::apply_event(&mut app, &stub(), answer);
        assert!(!screen(&mut app).contains("Resuming…"));
    }
}

#[tokio::test]
async fn another_job_ending_keeps_the_spinner_until_the_resume_answers() {
    let mut app = resuming();
    // A post-ready read or `/whoami` answer ends the command spinner.
    app.view.command_loading = false;
    assert!(screen(&mut app).contains("Resuming…"));
}

#[tokio::test]
async fn a_background_command_gets_its_label_back_once_resumed() {
    let mut app = picking();
    assert!(app.begin_command_loading());
    press(&mut app, KeyCode::Enter);
    assert!(screen(&mut app).contains("Resuming…"));
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Resumed {
            id: SAVED.into(),
            state: saved_state(),
        },
    );
    let screen = screen(&mut app);
    assert!(screen.contains("Loading…") && !screen.contains("Resuming…"));
}

#[tokio::test]
async fn a_turn_started_mid_resume_leaves_no_spinner_behind() {
    let mut app = resuming();
    generating(&mut app);
    assert!(!screen(&mut app).contains("Resuming…"));
    press(&mut app, KeyCode::Esc);
    app.set_status(Status::Ready);
    let screen = screen(&mut app);
    assert!(!screen.contains("Generating…") && !screen.contains("Resuming…"));
}

#[tokio::test]
async fn a_running_turn_keeps_its_own_loading_row() {
    let mut app = picking();
    generating(&mut app);
    press(&mut app, KeyCode::Enter);
    assert!(!screen(&mut app).contains("Resuming…"));
}

#[tokio::test]
async fn a_shell_command_owns_the_row_until_it_ends() {
    let mut app = resuming();
    app.session.shell_operation_id = Some("shell-1".into());
    assert!(!screen(&mut app).contains("Resuming…"));
    app.session.shell_operation_id = None;
    assert!(screen(&mut app).contains("Resuming…"));
}

#[tokio::test]
async fn a_failed_session_shows_no_spinner() {
    let mut app = resuming();
    app.set_status(Status::Failed);
    assert!(!screen(&mut app).contains("Resuming…"));
}

#[tokio::test]
async fn a_startup_picker_returns_to_initializing_when_cancelled() {
    let mut app = App::default();
    open_picker(&mut app);
    press(&mut app, KeyCode::Enter);
    assert!(screen(&mut app).contains("Resuming…"));
    press(&mut app, KeyCode::Esc);
    assert!(screen(&mut app).contains("Initializing…"));
}
