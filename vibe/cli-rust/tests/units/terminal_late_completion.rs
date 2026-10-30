//! Enqueue results can be reduced after their turn's completion notifications.

use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::message_queue::{self, QueueEvent, QueueItem};
use vibe_rs::server::notification;

use crate::support::{notify, queued, stub};

fn pending() -> QueueItem {
    QueueItem {
        queue_item_id: None,
        revision: 1,
        ..queued("message-1", "hi")
    }
}

fn started(app: &mut App, id: &str) {
    notify(
        app,
        notification::TURN_STARTED,
        json!({"turn": {"id": id, "queueItemId": "queue-1"}}),
    );
}

fn completed(app: &mut App, status: &str) {
    notify(
        app,
        notification::TURN_COMPLETED,
        json!({"turn": {"id": "turn-1", "status": status, "error":{"message":"Failed first line\nMore details"}, "queueItemId": "queue-1"}}),
    );
}

fn accepted(app: &mut App) {
    message_queue::apply_event(
        app,
        &stub(),
        QueueEvent::Accepted {
            message_id: "message-1".into(),
            queue_item_id: "queue-1".into(),
            session_id: "session".into(),
            images: Vec::new(),
            mentions: None,
        },
    );
}

fn app(focused: bool) -> App {
    let mut app = App::default();
    app.set_status(Status::Ready);
    app.terminal_notifier.configure(true, true);
    app.terminal_notifier.set_focus(focused);
    app.terminal_notifier.set_default_title("Current task");
    app.queue.items.push(pending());
    app
}

#[test]
fn enqueue_answer_after_completion_finalizes_title_and_bell_once() {
    for status in ["completed", "failed", "interrupted"] {
        for focused in [true, false] {
            let mut app = app(focused);
            // reader.rs routes the response first, but its queue task can deliver Accepted later.
            started(&mut app, "turn-1");
            completed(&mut app, status);
            assert_eq!(vibe_rs::terminal_notifier::title(&app), ">> Current task");
            assert!(!app.terminal_notifier.take_bell());
            accepted(&mut app);
            assert!(app.queue.is_empty() && app.is_idle());
            let rings = !focused && status != "interrupted";
            let title = if rings {
                "Current task - Task Complete"
            } else {
                "Current task"
            };
            assert_eq!(vibe_rs::terminal_notifier::title(&app), title);
            assert_eq!(app.terminal_notifier.take_bell(), rings);
            assert!(!app.terminal_notifier.take_bell());
            assert!(app.session.deferred_completion.is_none());
        }
    }
}

#[test]
fn dropping_a_promoted_prompt_settles_completion_once() {
    for status in ["completed", "failed", "interrupted"] {
        for rejected in [true, false] {
            let mut app = app(false);
            started(&mut app, "turn-1");
            completed(&mut app, status);
            assert!(!app.terminal_notifier.take_bell());
            if rejected {
                message_queue::apply_event(
                    &mut app,
                    &stub(),
                    QueueEvent::Rejected {
                        message_id: "message-1".into(),
                        error: None,
                    },
                );
            } else {
                app.queue.selected = Some("message-1".into());
                message_queue::remove_selected(&mut app, &stub());
            }
            assert!(app.queue.is_empty());
            let rings = status != "interrupted";
            assert_eq!(
                vibe_rs::terminal_notifier::title(&app),
                if rings {
                    "Current task - Task Complete"
                } else {
                    "Current task"
                }
            );
            assert_eq!(app.terminal_notifier.take_bell(), rings);
            assert!(app.session.deferred_completion.is_none());
            accepted(&mut app);
            assert!(!app.terminal_notifier.take_bell());
        }
    }
}

#[test]
fn a_new_turn_discards_the_old_deferred_completion() {
    let mut app = app(false);
    started(&mut app, "turn-1");
    completed(&mut app, "completed");
    notify(
        &mut app,
        notification::TURN_STARTED,
        json!({"turn": {"id": "turn-2"}}),
    );
    accepted(&mut app);
    assert!(matches!(app.session.status, Status::Generating { .. }));
    assert_eq!(vibe_rs::terminal_notifier::title(&app), ">> Current task");
    assert!(!app.terminal_notifier.take_bell());
    assert!(app.session.deferred_completion.is_none());
}

#[test]
fn the_late_answer_does_not_complete_remaining_queued_work() {
    let mut app = app(false);
    started(&mut app, "turn-1");
    let mut next = pending();
    next.message_id = "message-2".into();
    next.server_message_id = "message-2".into();
    next.queue_item_id = Some("queue-2".into());
    app.queue.items.push(next);
    completed(&mut app, "completed");
    accepted(&mut app);
    assert_eq!(app.queue.len(), 1);
    assert_eq!(vibe_rs::terminal_notifier::title(&app), ">> Current task");
    assert!(!app.terminal_notifier.take_bell());
    assert!(app.session.deferred_completion.is_none());
}

#[test]
fn the_late_answer_preserves_an_open_approval_without_a_completion_bell() {
    let mut app = app(false);
    started(&mut app, "turn-1");
    completed(&mut app, "completed");
    app.approval.open = true;
    accepted(&mut app);
    assert_eq!(vibe_rs::terminal_notifier::title(&app), "? Current task");
    assert!(!app.terminal_notifier.take_bell());
}

#[test]
fn dismissing_a_dialog_after_a_late_enqueue_restores_the_outcome() {
    use vibe_rs::program_status::{encode_report, BlockedKind, ProgramState};
    for (status, outcome) in [
        ("completed", ProgramState::Done),
        ("failed", ProgramState::Error),
    ] {
        let mut app = app(false);
        app.terminal_notifier.enable_program_reports(true);
        started(&mut app, "turn-1");
        completed(&mut app, status);
        vibe_rs::question_app::on_callback_call(
            &mut app,
            &json!({"callback":{"callbackId":"q","detail":{"kind":"user_input","request":{"questions":[{"question":"Pick?","options":[]}]}}}}),
        );
        assert!(app.terminal_notifier.take_bell());
        accepted(&mut app);
        assert_eq!(
            vibe_rs::terminal_notifier::take_program_report(&mut app),
            Some(encode_report(
                ProgramState::Blocked(BlockedKind::Question),
                "Pick?"
            ))
        );
        vibe_rs::question_app::dismiss(
            &mut app,
            &vibe_rs::question_app::QuestionSource::Callback(vibe_rs::server::CallbackAddress {
                callback_id: "q".into(),
                ..Default::default()
            }),
        );
        assert_eq!(
            vibe_rs::terminal_notifier::take_program_report(&mut app),
            Some(encode_report(
                outcome,
                if outcome == ProgramState::Done {
                    "Current task"
                } else {
                    "Failed first line"
                }
            ))
        );
        assert!(!app.terminal_notifier.take_bell());
    }
}

#[test]
fn session_handoff_discards_the_old_deferred_completion() {
    let mut app = app(false);
    started(&mut app, "turn-1");
    completed(&mut app, "completed");
    app.set_session_id("another-session".into());
    accepted(&mut app);
    assert!(app.session.deferred_completion.is_none());
    assert!(!app.terminal_notifier.take_bell());
}

#[test]
fn a_replacement_settling_after_completion_finalizes_the_consumed_edit() {
    let mut app = app(false);
    app.queue.items[0].queue_item_id = Some("queue-1".into());
    app.queue.items[0].sent = false;
    app.queue.items[0].replacing = true;
    started(&mut app, "turn-1");
    completed(&mut app, "completed");
    message_queue::apply_event(
        &mut app,
        &stub(),
        QueueEvent::GroupReplaced {
            server_message_id: "message-1".into(),
            revision: 1,
            covered: vec![("message-1".into(), "hi".into(), Vec::new(), None)],
            delivered: vec!["message-1".into()],
            outcome: message_queue::ReplacementOutcome::Replaced,
        },
    );
    assert!(app.queue.is_empty());
    assert_eq!(
        vibe_rs::terminal_notifier::title(&app),
        "Current task - Task Complete"
    );
    assert!(app.terminal_notifier.take_bell());
    assert!(app.session.deferred_completion.is_none());
}
