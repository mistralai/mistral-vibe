//! Server-pushed `warning`/`error`/`turn/retrying` notifications reach the UI,
//! matching the legacy Python CLI (VIBE-4622).

use std::sync::Arc;
use std::time::Instant;

use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::Terminal;
use serde_json::json;
use vibe_rs::app::{App, Status, ToastSeverity};
use vibe_rs::event_handler;
use vibe_rs::server::{notification, Client, Notification};
use vibe_rs::ui;

fn notif(method: &str, params: serde_json::Value) -> Notification {
    Notification {
        method: method.to_owned(),
        params,
    }
}

#[test]
fn warning_notification_shows_a_warning_toast() {
    let mut app = App::default();
    app.set_status(Status::Ready);
    let client = Arc::new(Client::stub());

    let event = notif(
        notification::WARNING,
        json!({"warning": {"code": "warning", "message": "config is deprecated"}}),
    );
    event_handler::apply_notification(&mut app, &client, &event);

    let toast = app
        .overlays
        .toasts
        .back()
        .expect("warning must surface a toast");
    assert_eq!(toast.text, "config is deprecated");
    assert!(matches!(toast.severity, ToastSeverity::Warning));
}

#[test]
fn error_notification_shows_an_error_toast() {
    let mut app = App::default();
    app.set_status(Status::Ready);
    let client = Arc::new(Client::stub());

    let event = notif(
        notification::ERROR,
        json!({"error": {"code": "error", "message": "backend unavailable"}}),
    );
    event_handler::apply_notification(&mut app, &client, &event);

    let toast = app
        .overlays
        .toasts
        .back()
        .expect("error must surface a toast");
    assert_eq!(toast.text, "backend unavailable");
    assert!(matches!(toast.severity, ToastSeverity::Error));
}

#[test]
fn a_warning_then_error_keeps_both_toasts_in_one_batch() {
    let mut app = App::default();
    app.set_status(Status::Ready);
    let client = Arc::new(Client::stub());

    for event in [
        notif(
            notification::WARNING,
            json!({"warning": {"code": "warning", "message": "first"}}),
        ),
        notif(
            notification::ERROR,
            json!({"error": {"code": "error", "message": "second"}}),
        ),
    ] {
        event_handler::apply_notification(&mut app, &client, &event);
    }

    let texts: Vec<&str> = app
        .overlays
        .toasts
        .iter()
        .map(|t| t.text.as_str())
        .collect();
    assert_eq!(texts, vec!["first", "second"]);
}

#[test]
fn toast_overflow_is_reported() {
    let mut app = App::default();
    for index in 0..65 {
        app.show_toast(format!("notice {index}"), ToastSeverity::Warning, 5);
    }
    let mut terminal = Terminal::new(TestBackend::new(120, 30)).expect("terminal");

    terminal
        .draw(|frame| ui::draw(&mut app, frame))
        .expect("draw");

    let screen: String = terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect();
    assert!(screen.contains("1 earlier notification omitted"));
}

#[test]
fn offscreen_toasts_start_expiring_only_after_they_render() {
    let mut app = App::default();
    app.show_toast("older".into(), ToastSeverity::Warning, 5);
    app.show_toast("newer".into(), ToastSeverity::Warning, 5);
    let mut terminal = Terminal::new(TestBackend::new(80, 10)).expect("terminal");

    terminal
        .draw(|frame| ui::toast::draw(&mut app, frame, Rect::new(0, 4, 80, 0)))
        .expect("draw");

    assert!(app.overlays.toasts.front().unwrap().until.is_none());
    assert!(app.overlays.toasts.back().unwrap().until.is_some());
}

#[test]
fn oversized_toast_starts_expiring_even_if_it_cannot_paint() {
    let mut app = App::default();
    app.show_toast("line\n".repeat(20), ToastSeverity::Warning, 5);
    let mut terminal = Terminal::new(TestBackend::new(80, 10)).expect("terminal");

    terminal
        .draw(|frame| ui::toast::draw(&mut app, frame, Rect::new(0, 3, 80, 0)))
        .expect("draw");

    assert!(app.overlays.toasts.back().unwrap().until.is_some());
}

#[test]
fn too_narrow_terminal_starts_the_newest_toast_timer() {
    let mut app = App::default();
    app.show_toast("warning".into(), ToastSeverity::Warning, 5);
    let mut terminal = Terminal::new(TestBackend::new(8, 10)).expect("terminal");

    terminal
        .draw(|frame| ui::toast::draw(&mut app, frame, Rect::new(0, 8, 8, 0)))
        .expect("draw");

    assert!(app.overlays.toasts.back().unwrap().until.is_some());
}

#[test]
fn omitted_count_stays_with_the_overflow_toast() {
    let mut app = App::default();
    for index in 0..65 {
        app.show_toast(format!("notice {index}"), ToastSeverity::Warning, 5);
    }
    app.overlays.toasts.pop_back();
    app.show_toast("later".into(), ToastSeverity::Warning, 5);
    let mut terminal = Terminal::new(TestBackend::new(120, 30)).expect("terminal");

    terminal
        .draw(|frame| ui::draw(&mut app, frame))
        .expect("draw");

    let screen: String = terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect();
    assert!(!screen.contains("omitted"));
    assert!(screen.contains("later"));
}

#[test]
fn turn_retrying_notification_is_ignored() {
    // ADR 0009: the compatibility `turn/retrying` notification must not drive the
    // label; retry state derives from `PublicSessionState.retrying` on snapshots.
    let mut app = App::default();
    app.set_status(Status::Generating {
        since: Instant::now(),
    });
    let before = app.view.loading.label().to_owned();
    let client = Arc::new(Client::stub());

    let event = notif(
        notification::TURN_RETRYING,
        json!({"sessionId": "s", "category": "rate_limited", "detail": "429"}),
    );
    event_handler::apply_notification(&mut app, &client, &event);

    assert_eq!(app.view.loading.label(), before);
    assert_ne!(app.view.loading.label(), "Retrying");
}
