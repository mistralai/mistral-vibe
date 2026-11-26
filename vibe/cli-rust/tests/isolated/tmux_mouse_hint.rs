//! Inside tmux with `mouse off`, a startup warning explains scrolling until Ctrl+X then `h` dismisses it for good.
#![cfg(unix)]

use std::os::unix::fs::PermissionsExt;
use std::path::Path;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::server::{HistoryEntry, MessageContent};
use vibe_rs::telemetry::{self, event, TelemetryEvent};
use vibe_rs::{notices, tmux, tmux_hint};

const CTRL_X: KeyEvent = KeyEvent::new(KeyCode::Char('x'), KeyModifiers::CONTROL);
const H: KeyEvent = KeyEvent::new(KeyCode::Char('h'), KeyModifiers::NONE);

/// Press Ctrl+X then `h`; true when `h` hid the warning.
fn ctrl_x_h(app: &mut App) -> bool {
    assert!(
        !tmux_hint::handle_key(app, &CTRL_X),
        "Ctrl+X still reaches the rail"
    );
    tmux_hint::handle_key(app, &H)
}

/// Point the fake tmux at `answer` for its `mouse` option and re-read it.
fn set_tmux_mouse(dir: &Path, answer: &str) {
    std::fs::write(dir.join("mouse"), answer).expect("write tmux answer");
    tmux::refresh_mouse();
}

/// Enter a tmux session answering `mouse off`, with a temporary `VIBE_HOME`.
fn mouse_off_session() -> tempfile::TempDir {
    let dir = tempfile::tempdir().expect("temp dir");
    let script = dir.path().join("tmux");
    let answer = dir.path().join("mouse");
    std::fs::write(
        &script,
        format!("#!/bin/sh\n/bin/cat '{}'\n", answer.display()),
    )
    .expect("write fake tmux");
    std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755))
        .expect("make fake tmux executable");
    // macOS scans a new executable on its first run, which can exceed the probe timeout under load.
    let _ = std::process::Command::new(&script).output();
    std::env::set_var("PATH", dir.path());
    std::env::set_var("TMUX", "/tmp/tmux-test/default,1,0");
    std::env::set_var("TMUX_PANE", "%3");
    std::env::set_var("VIBE_HOME", dir.path());
    set_tmux_mouse(dir.path(), "off");
    assert!(tmux::mouse_off());
    dir
}

/// Whether a transcript message carries the warning; the box escapes its punctuation.
fn shown(app: &App) -> bool {
    const LEAD: &str = "tmux has mouse off";
    assert!(tmux_hint::text().starts_with(LEAD));
    app.view.transcript.lines().any(|entry| match entry.entry {
        HistoryEntry::Message(message) => message
            .content
            .iter()
            .any(|block| matches!(block, MessageContent::Text { text } if text.contains(LEAD))),
        _ => false,
    })
}

/// Collect the app's analytics events instead of sending them.
fn record_telemetry(app: &mut App) -> mpsc::Receiver<TelemetryEvent> {
    let (tx, rx) = mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(tx);
    rx
}

/// The events queued so far, oldest first.
fn recorded(rx: &mut mpsc::Receiver<TelemetryEvent>) -> Vec<TelemetryEvent> {
    std::iter::from_fn(|| rx.try_recv().ok()).collect()
}

#[test]
fn ctrl_x_then_h_dismisses_the_warning_for_later_sessions() {
    let _home = mouse_off_session();
    let mut app = App::default();

    tmux_hint::show(&mut app);
    assert!(shown(&app));

    assert!(ctrl_x_h(&mut app));
    assert!(!shown(&app));
    assert!(!ctrl_x_h(&mut app));

    let mut next = App::default();
    tmux_hint::show(&mut next);
    assert!(!shown(&next));
}

#[test]
fn mouse_capture_turned_off_needs_no_warning() {
    let _home = mouse_off_session();
    let mut app = App::default();
    app.session.startup_config.enable_mouse_capture = false;

    tmux_hint::show(&mut app);

    assert!(!shown(&app));
    assert!(!ctrl_x_h(&mut app));
}

#[test]
fn only_h_right_after_ctrl_x_dismisses_the_warning() {
    let _home = mouse_off_session();
    let mut app = App::default();
    tmux_hint::show(&mut app);

    assert!(
        !tmux_hint::handle_key(&mut app, &H),
        "h alone types into the prompt"
    );
    tmux_hint::handle_key(&mut app, &CTRL_X);
    assert!(!tmux_hint::handle_key(
        &mut app,
        &KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE)
    ));
    assert!(
        !tmux_hint::handle_key(&mut app, &H),
        "another key in between disarms h"
    );
    assert!(shown(&app));

    assert!(ctrl_x_h(&mut app));
    assert!(!shown(&app));
}

#[test]
fn a_closed_startup_box_gets_no_warning_row() {
    let _home = mouse_off_session();
    let mut app = App::default();
    notices::close_startup(&mut app);

    tmux_hint::show(&mut app);

    assert!(!shown(&app));
}

#[test]
fn tmux_turning_its_mouse_on_drops_the_warning_without_dismissing_it() {
    let home = mouse_off_session();
    let mut app = App::default();
    tmux_hint::show(&mut app);

    tmux_hint::refresh(&mut app);
    assert!(shown(&app));
    set_tmux_mouse(home.path(), "on");
    tmux_hint::refresh(&mut app);
    assert!(!shown(&app));

    set_tmux_mouse(home.path(), "off");
    let mut next = App::default();
    tmux_hint::show(&mut next);
    assert!(shown(&next));
}

#[test]
fn turning_mouse_capture_off_drops_the_warning() {
    let _home = mouse_off_session();
    let mut app = App::default();
    tmux_hint::show(&mut app);

    app.session.startup_config.enable_mouse_capture = false;
    tmux_hint::refresh(&mut app);

    assert!(!shown(&app));
}

#[test]
fn the_warning_records_its_impression_and_its_dismissal() {
    let _home = mouse_off_session();
    let mut app = App::default();
    let mut rx = record_telemetry(&mut app);

    tmux_hint::show(&mut app);
    tmux_hint::show(&mut app);
    let shown: Vec<_> = recorded(&mut rx).iter().map(|e| e.name).collect();
    assert_eq!(shown, [event::TMUX_MOUSE_HINT_RENDERED]);

    assert!(ctrl_x_h(&mut app));
    let dismissed = recorded(&mut rx);
    assert_eq!(dismissed.len(), 1);
    assert_eq!(dismissed[0].name, event::TMUX_MOUSE_HINT_DISABLED);
    assert_eq!(dismissed[0].properties["entry_point"], "rail_h");
}

#[test]
fn a_warning_that_never_shows_or_is_withdrawn_records_nothing_more() {
    let home = mouse_off_session();
    let mut app = App::default();
    let mut rx = record_telemetry(&mut app);
    notices::close_startup(&mut app);
    tmux_hint::show(&mut app);
    assert!(!ctrl_x_h(&mut app));
    assert!(recorded(&mut rx).is_empty());

    let mut next = App::default();
    let mut next_rx = record_telemetry(&mut next);
    tmux_hint::show(&mut next);
    set_tmux_mouse(home.path(), "on");
    tmux_hint::refresh(&mut next);
    let names: Vec<_> = recorded(&mut next_rx).iter().map(|e| e.name).collect();
    assert_eq!(names, [event::TMUX_MOUSE_HINT_RENDERED]);
}
