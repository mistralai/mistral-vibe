#![cfg(unix)]
//! Ctrl+Z suspension input.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::{app::App, input::request_suspend};

#[test]
fn ctrl_z_requests_suspension() {
    let mut app = App::default();
    let key = KeyEvent::new(KeyCode::Char('z'), KeyModifiers::CONTROL);

    assert!(request_suspend(&mut app, &key));
    assert!(app.suspend_requested);
}
