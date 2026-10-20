//! Job-control keys only request suspension on platforms with Unix job control.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::{app::App, input::request_suspend};

#[test]
fn ctrl_z_only_requests_unix_job_control() {
    let mut app = App::default();
    app.chat_input.input = "keep my draft".into();
    let key = KeyEvent::new(KeyCode::Char('z'), KeyModifiers::CONTROL);

    assert_eq!(request_suspend(&mut app, &key), cfg!(unix));
    assert_eq!(app.suspend_requested, cfg!(unix));
    assert_eq!(app.chat_input.input, "keep my draft");
}

#[test]
fn plain_z_does_not_suspend() {
    let mut app = App::default();
    let key = KeyEvent::new(KeyCode::Char('z'), KeyModifiers::NONE);

    assert!(!request_suspend(&mut app, &key));
    assert!(!app.suspend_requested);
}
