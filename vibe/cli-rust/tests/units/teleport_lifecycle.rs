//! Teleport progress, completion, failure, cancellation, and session changes.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::commands::CommandEvent;
use vibe_rs::question_app::QuestionSource;
use vibe_rs::server::{UserAnswer, UserQuestionResult};
use vibe_rs::teleport::{self, Failure, Reply};

use crate::teleport_support::{emit, event, op_id, rows, running, stub};

#[test]
fn progress_events_update_the_status_row_and_keep_the_app_busy() {
    let mut app = running();
    emit(&mut app, json!({"kind": "checking_git"}));
    assert_eq!(
        rows(&app),
        [(
            "teleport_status".into(),
            "Preparing workspace...".into(),
            true
        )]
    );
    assert!(teleport::busy(&app));
    assert!(!app.is_idle());
    assert!(teleport::loading_since(&app).is_some());
}

#[test]
fn events_for_another_operation_are_ignored() {
    let mut app = running();
    let other = event("other", json!({"kind": "complete", "url": "https://x"}));
    teleport::on_event(&mut app, &stub(), &other);
    assert!(app.teleport.is_some());
    assert_eq!(rows(&app)[0].1, "Teleporting...");
}

#[test]
fn push_required_asks_locally_then_answering_resumes() {
    let mut app = running();
    emit(
        &mut app,
        json!({"kind": "push_required", "unpushedCount": 2}),
    );
    assert!(app.question_app.open);
    assert_eq!(app.question_app.source, Some(QuestionSource::TeleportPush));
    assert!(teleport::loading_since(&app).is_none());
    assert!(!teleport::busy(&app));

    teleport::answer_push(
        &mut app,
        &stub(),
        &UserQuestionResult {
            answers: vec![UserAnswer {
                question: String::new(),
                answer: teleport::PUSH_LABEL.into(),
                is_other: false,
            }],
            cancelled: false,
        },
    );
    assert!(teleport::busy(&app));
    assert_eq!(rows(&app)[0].1, "Teleporting...");
}

#[test]
fn push_required_without_a_count_still_asks() {
    let mut app = running();
    emit(&mut app, json!({"kind": "push_required"}));
    assert_eq!(app.question_app.source, Some(QuestionSource::TeleportPush));
}

#[test]
fn push_required_declines_instead_of_replacing_a_callback_question() {
    let mut app = running();
    app.chat_input.last_keystroke = Some(std::time::Instant::now());
    let callback = QuestionSource::Callback("callback".into());
    app.question_app.pending = Some((callback.clone(), teleport::push_question(1, false)));
    emit(
        &mut app,
        json!({"kind": "push_required", "unpushedCount": 2}),
    );
    assert!(matches!(&app.question_app.pending, Some((source, _)) if *source == callback));
    assert!(teleport::busy(&app));
}

#[test]
fn complete_settles_the_row_with_the_url() {
    let mut app = running();
    emit(
        &mut app,
        json!({"kind": "complete", "url": "https://chat.example/code/1"}),
    );
    assert!(app.teleport.is_none());
    assert!(app.is_idle());
    assert_eq!(
        rows(&app),
        [(
            "teleport_complete".into(),
            "https://chat.example/code/1".into(),
            false
        )]
    );
}

#[test]
fn failure_replaces_the_row_with_an_error() {
    let mut app = running();
    emit(
        &mut app,
        json!({"kind": "failed", "error": {"message": "Teleport cancelled: changes not pushed."}}),
    );
    assert!(app.teleport.is_none());
    assert_eq!(
        rows(&app),
        [(
            "command_error".into(),
            "Teleport cancelled: changes not pushed.".into(),
            false
        )]
    );
}

#[test]
fn escape_cancels_and_a_confirmed_cancel_settles_the_row() {
    let mut app = running();
    teleport::interrupt(&mut app, &stub());
    assert!(app.teleport.as_ref().is_some_and(|op| op.cancelling));
    let operation_id = op_id(&app);
    teleport::apply_reply(
        &mut app,
        &stub(),
        Reply::Cancelled {
            operation_id,
            cancelled: true,
        },
    );
    assert!(app.teleport.is_none());
    assert_eq!(
        rows(&app),
        [("teleport_status".into(), "Teleport cancelled".into(), false)]
    );
}

#[test]
fn ctrl_c_cancels_the_teleport_as_its_hint_says() {
    let mut app = running();
    let ctrl_c = KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL);
    assert_eq!(
        vibe_rs::input::handle_priority_key(&mut app, &stub(), ctrl_c),
        Some(false)
    );
    assert!(app.teleport.as_ref().is_some_and(|op| op.cancelling));
    assert!(app.quit.active().is_none());
}

#[tokio::test]
async fn a_rejected_request_cancels_the_server_operation() {
    let mut app = running();
    let (tx, mut rx) = mpsc::channel::<CommandEvent>(4);
    app.command_tx = Some(tx);
    let operation_id = op_id(&app);
    teleport::apply_reply(
        &mut app,
        &stub(),
        Reply::Answered {
            operation_id: operation_id.clone(),
            failure: Some(Failure {
                code: None,
                message: "boom".into(),
            }),
        },
    );
    assert!(app.teleport.is_none());
    assert_eq!(rows(&app), [("command_error".into(), "boom".into(), false)]);
    let Some(CommandEvent::Teleport(reply)) = rx.recv().await else {
        panic!("expected a teleport cancel reply");
    };
    assert!(matches!(*reply, Reply::Cancelled { operation_id: id, .. } if id == operation_id));
}

#[test]
fn a_cancel_racing_push_required_closes_the_push_question() {
    let mut app = running();
    teleport::interrupt(&mut app, &stub());
    emit(
        &mut app,
        json!({"kind": "push_required", "unpushedCount": 1}),
    );
    assert!(app.question_app.open);
    let operation_id = op_id(&app);
    teleport::apply_reply(
        &mut app,
        &stub(),
        Reply::Cancelled {
            operation_id,
            cancelled: true,
        },
    );
    assert!(app.teleport.is_none());
    assert!(!app.question_app.open);
    assert_ne!(app.question_app.source, Some(QuestionSource::TeleportPush));
}

#[test]
fn a_new_session_drops_a_push_question_still_waiting_to_open() {
    let mut app = running();
    app.chat_input.last_keystroke = Some(std::time::Instant::now());
    emit(
        &mut app,
        json!({"kind": "push_required", "unpushedCount": 1}),
    );
    assert!(app.question_app.pending.is_some());
    app.set_session_id("other".into());
    assert!(app.question_app.pending.is_none());
}

#[test]
fn a_refused_cancel_keeps_following_the_operation() {
    let mut app = running();
    teleport::interrupt(&mut app, &stub());
    let operation_id = op_id(&app);
    teleport::apply_reply(
        &mut app,
        &stub(),
        Reply::Cancelled {
            operation_id,
            cancelled: false,
        },
    );
    assert!(app.teleport.as_ref().is_some_and(|op| !op.cancelling));
}

#[test]
fn a_new_session_abandons_the_teleport() {
    let mut app = running();
    app.set_session_id("other".into());
    assert!(app.teleport.is_none());
    assert_eq!(
        rows(&app),
        [(
            "command_error".into(),
            "Teleport was cancelled because the session changed".into(),
            false
        )]
    );
}
