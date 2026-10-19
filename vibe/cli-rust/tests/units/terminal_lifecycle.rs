//! Turn, queue, and dialog transitions preserve ordinary titles and bells.

use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Instant;
use vibe_rs::app::{App, Status};
use vibe_rs::server::{notification, Client, Notification};
use vibe_rs::{approval, message_queue, question_app};

fn app() -> App {
    let mut app = App::default();
    app.terminal_notifier.configure(true, true);
    app.terminal_notifier.set_focus(false);
    app.set_status(Status::Generating {
        since: Instant::now(),
    });
    app
}

fn notify(app: &mut App, method: &str, params: Value) {
    assert!(vibe_rs::event_handler::apply_notification(
        app,
        &Arc::new(Client::stub()),
        &Notification {
            method: method.into(),
            params,
        },
    ));
}

fn completed(app: &mut App, status: &str) {
    notify(
        app,
        notification::TURN_COMPLETED,
        json!({"turn": {"status": status}}),
    );
}

fn queue(paused: bool) -> Value {
    json!({"paused": paused, "items": [{
        "id": "next", "entries": [{"entryId": "next-message", "role": "user",
            "content": [{"type": "text", "text": "Continue"}]}]
    }]})
}

fn assert_title_without_bell(app: &mut App, title: &str) {
    assert_eq!(vibe_rs::terminal_notifier::title(app), title);
    assert!(!app.terminal_notifier.take_bell());
}

fn approval(app: &mut App) {
    app.approval
        .enqueue(vibe_rs::server::ApprovalCallback {
            callback_id: "approval1".into(),
            ..Default::default()
        })
        .unwrap();
    approval::show_pending(app);
}

fn question(app: &mut App) {
    question_app::on_callback_call(
        app,
        &json!({
            "callback": {"callbackId": "question1", "detail": {"kind": "user_input", "request": {
                "questions": [{"question": "Pick?", "options": []}]
            }}}
        }),
    );
}

#[test]
fn completed_and_failed_turns_keep_unpaused_queue_working() {
    for status in ["completed", "failed"] {
        let mut app = app();
        message_queue::sync(&mut app, &queue(false));
        completed(&mut app, status);
        assert_title_without_bell(&mut app, ">> Vibe");
        assert!(matches!(app.session.status, Status::Ready), "{status}");
    }
}

#[test]
fn paused_queue_does_not_hide_genuine_completion() {
    let mut app = app();
    message_queue::sync(&mut app, &queue(true));
    completed(&mut app, "completed");
    assert_eq!(
        vibe_rs::terminal_notifier::title(&app),
        "Vibe - Task Complete"
    );
    assert!(app.terminal_notifier.take_bell());
    app.terminal_notifier.set_focus(true);
    assert_title_without_bell(&mut app, "Vibe");
}

#[test]
fn interrupted_turn_does_not_ring_and_keeps_remaining_queue_working() {
    for paused in [false, true] {
        let mut app = app();
        message_queue::sync(&mut app, &queue(paused));
        completed(&mut app, "interrupted");
        assert_title_without_bell(&mut app, if paused { "Vibe" } else { ">> Vibe" });
        assert!(matches!(app.session.status, Status::Ready));
    }
}

#[tokio::test]
async fn reachable_incomplete_stream_retry_returns_before_completion() {
    let mut app = app();
    app.session.session_id = Some("session".into());
    let (tx, _rx) = tokio::sync::mpsc::channel(16);
    app.command_tx = Some(tx);
    notify(
        &mut app,
        notification::TURN_COMPLETED,
        json!({"turn": {"status": "failed", "error": {"code": "incomplete_stream"}}}),
    );
    assert_eq!(app.session.incomplete_stream_retries, 1);
    assert!(matches!(app.session.status, Status::Generating { .. }));
    assert_title_without_bell(&mut app, ">> Vibe");
}

#[test]
fn queue_updates_pause_and_resume_work_without_a_bell() {
    let mut app = app();
    app.set_status(Status::Ready);
    for (queue, title) in [
        (queue(false), ">> Vibe"),
        (queue(true), "Vibe"),
        (queue(false), ">> Vibe"),
    ] {
        notify(
            &mut app,
            notification::TURN_QUEUE_UPDATED,
            json!({"queue": queue}),
        );
        assert_title_without_bell(&mut app, title);
    }
}

#[test]
fn resuming_a_paused_queue_restores_the_running_title_without_a_bell() {
    let mut app = app();
    app.set_status(Status::Ready);
    message_queue::sync(&mut app, &queue(true));
    assert_title_without_bell(&mut app, "Vibe");
    message_queue::resume(&mut app, &Arc::new(Client::stub()));
    assert!(!app.queue.paused);
    assert_title_without_bell(&mut app, ">> Vibe");
}

#[test]
fn removing_the_last_queued_turn_restores_idle_without_a_bell() {
    let mut app = app();
    app.set_status(Status::Ready);
    message_queue::sync(&mut app, &queue(false));
    app.queue.selected = Some("next-message".into());
    message_queue::remove_selected(&mut app, &Arc::new(Client::stub()));
    assert!(app.queue.is_empty());
    assert_title_without_bell(&mut app, "Vibe");
}

#[test]
fn answering_approval_restores_the_remaining_question_without_a_bell() {
    let mut app = app();
    app.terminal_notifier.set_focus(true);
    question(&mut app);
    approval(&mut app);
    assert!(app.question_app.open && app.approval.open);
    assert_title_without_bell(&mut app, "? Vibe");
    app.terminal_notifier.set_focus(false);
    approval::apply_event(
        &mut app,
        approval::Event::Responded {
            callback_id: "approval1".into(),
        },
    );
    assert!(app.question_app.open && !app.approval.open);
    assert_title_without_bell(&mut app, "? Vibe");
}

#[test]
fn dismissing_a_question_preserves_the_remaining_approval_without_a_bell() {
    let mut app = app();
    app.terminal_notifier.set_focus(true);
    approval(&mut app);
    question(&mut app);
    assert!(app.approval.open && app.question_app.open);
    app.terminal_notifier.set_focus(false);
    question_app::dismiss(
        &mut app,
        &question_app::QuestionSource::Callback("question1".into()),
    );
    assert!(app.approval.open && !app.question_app.open);
    assert_title_without_bell(&mut app, "? Vibe");
}

#[test]
fn queue_transitions_preserve_open_dialog_titles_without_a_bell() {
    for show_dialog in [approval, question] {
        let mut app = app();
        app.terminal_notifier.set_focus(true);
        show_dialog(&mut app);
        app.terminal_notifier.set_focus(false);
        message_queue::sync(&mut app, &queue(false));
        completed(&mut app, "completed");
        assert_title_without_bell(&mut app, "? Vibe");
        notify(
            &mut app,
            notification::TURN_QUEUE_UPDATED,
            json!({"queue": queue(true)}),
        );
        assert_title_without_bell(&mut app, "? Vibe");
        message_queue::resume(&mut app, &Arc::new(Client::stub()));
        assert_title_without_bell(&mut app, "? Vibe");
        app.queue.selected = Some("next-message".into());
        message_queue::remove_selected(&mut app, &Arc::new(Client::stub()));
        assert_title_without_bell(&mut app, "? Vibe");
    }
}
