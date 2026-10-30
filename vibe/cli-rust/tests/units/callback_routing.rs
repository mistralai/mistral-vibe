//! Every callback kind is answered at the session that raised it, root or subagent.

use std::sync::atomic::AtomicU64;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::question_app::MAX_PENDING_QUESTIONS;
use vibe_rs::server::{
    callback, method, server_method, ActiveSession, CallbackAddress, CallbackOutput, Client,
    Pending, CALLBACK_KINDS,
};
use vibe_rs::ui_event::{self, UiEvent};
use vibe_rs::{approval, question_app};

use crate::support::{key, with_status};

const ROOT: &str = "session-root";
const TITLE: &str = "User input required";

fn app() -> (App, mpsc::Receiver<UiEvent>) {
    let mut app = with_status(Status::Generating {
        since: std::time::Instant::now(),
    });
    app.session.session_id = Some(ROOT.into());
    let (events, received) = mpsc::channel(8);
    app.events = Some(events);
    (app, received)
}

fn callback(kind: &str, callback_id: &str, session_id: Value) -> Value {
    json!({"callback": {
        "callbackId": callback_id,
        "sessionId": session_id,
        "title": TITLE,
        "detail": {
            "kind": kind,
            "request": {"questions": [{"question": "Proceed?", "options": [{"label": "Yes"}]}]},
        },
    }})
}

fn ask(app: &mut App, callback_id: &str, session_id: Value) -> bool {
    question_app::on_callback_call(app, &callback("user_input", callback_id, session_id))
}

fn answer_question(app: &mut App, client: &Arc<Client>) {
    assert!(app.question_app.open);
    app.question_app.mount_time = None;
    question_app::select(app, client);
}

async fn result_target(frames: &mut mpsc::UnboundedReceiver<Value>) -> (String, String) {
    let frame = tokio::time::timeout(Duration::from_secs(5), frames.recv())
        .await
        .expect("no callback/result was sent")
        .expect("callback/result frame");
    assert_eq!(frame["method"], method::CALLBACK_RESULT);
    let params = &frame["params"];
    (
        params["sessionId"].as_str().unwrap().to_owned(),
        params["result"]["callbackId"].as_str().unwrap().to_owned(),
    )
}

fn answering() -> (Arc<Client>, mpsc::UnboundedReceiver<Value>) {
    let (client, frames) = Client::stub_answering();
    (Arc::new(client), frames)
}

#[tokio::test]
async fn a_subagent_question_is_answered_at_the_subagent_session() {
    let (mut app, _events) = app();
    let (client, mut frames) = answering();
    ask(&mut app, "q1", json!("child"));

    answer_question(&mut app, &client);

    assert_eq!(
        result_target(&mut frames).await,
        ("child".into(), "q1".into())
    );
}

#[tokio::test]
async fn a_question_without_a_session_falls_back_to_the_root() {
    for session_id in [Value::Null, json!("")] {
        let (mut app, _events) = app();
        let (client, mut frames) = answering();
        ask(&mut app, "q1", session_id);

        answer_question(&mut app, &client);

        assert_eq!(result_target(&mut frames).await, (ROOT.into(), "q1".into()));
    }
}

#[tokio::test]
async fn a_subagent_approval_is_answered_at_the_subagent_session() {
    let (mut app, _events) = app();
    let (client, mut frames) = answering();
    app.set_status(Status::Ready);
    assert!(approval::on_callback_call(
        &mut app,
        &callback("approval", "a1", json!("child"))
    ));
    app.approval.mount_time = None;

    approval::handle_key(&mut app, &client, key(KeyCode::Enter));

    assert_eq!(
        result_target(&mut frames).await,
        ("child".into(), "a1".into())
    );
}

#[tokio::test]
async fn concurrent_questions_queue_and_each_answer_reaches_its_session() {
    let (mut app, mut events) = app();
    let (client, mut frames) = answering();
    ask(&mut app, "q1", json!("child-a"));
    ask(&mut app, "q2", json!("child-b"));
    ask(&mut app, "q1", json!("child-a"));
    assert_eq!(app.question_app.pending.len(), 1);

    answer_question(&mut app, &client);
    assert_eq!(
        result_target(&mut frames).await,
        ("child-a".into(), "q1".into())
    );
    let accepted = events.recv().await.expect("first reply");
    ui_event::apply(&mut app, &client, accepted);
    assert_eq!(app.view.loading.label(), TITLE);

    answer_question(&mut app, &client);
    assert_eq!(
        result_target(&mut frames).await,
        ("child-b".into(), "q2".into())
    );
    assert!(!app.question_app.open);
    assert_eq!(app.view.loading.label(), TITLE);
    let accepted = events.recv().await.expect("second reply");
    ui_event::apply(&mut app, &client, accepted);
    assert_ne!(app.view.loading.label(), TITLE);
}

#[test]
fn the_question_backlog_is_bounded_and_defers_delivery() {
    let (mut app, _events) = app();
    for index in 0..=MAX_PENDING_QUESTIONS {
        assert!(ask(&mut app, &format!("q{index}"), json!("child")));
    }
    assert_eq!(app.question_app.pending.len(), MAX_PENDING_QUESTIONS);
    assert!(!ask(&mut app, "overflow", json!("child")));
    assert!(ask(&mut app, "q0", json!("child")));
}

#[tokio::test]
async fn a_rejected_answer_reopens_the_question_and_a_rejected_cancel_drops_it() {
    let (mut app, mut events) = app();
    let client = Arc::new(Client::stub());
    ask(&mut app, "q1", json!("child"));

    answer_question(&mut app, &client);
    let event = events.recv().await.expect("answer reply");
    ui_event::apply(&mut app, &client, event);
    assert!(app.question_app.open);
    assert_eq!(app.overlays.toasts.len(), 1);
    assert_eq!(app.view.loading.label(), TITLE);

    app.question_app.mount_time = None;
    question_app::cancel(&mut app, &client);
    let event = events.recv().await.expect("cancel reply");
    ui_event::apply(&mut app, &client, event);
    assert!(!app.question_app.open);
    assert!(app.question_app.pending.is_empty());
    assert_ne!(app.view.loading.label(), TITLE);
}

#[tokio::test]
async fn a_rejected_answer_is_not_reopened_twice_after_a_redelivery() {
    let (mut app, mut events) = app();
    let client = Arc::new(Client::stub());
    ask(&mut app, "q1", json!("child"));
    answer_question(&mut app, &client);
    ask(&mut app, "q1", json!("child"));

    let event = events.recv().await.expect("answer reply");
    ui_event::apply(&mut app, &client, event);

    assert!(app.question_app.open);
    assert!(app.question_app.pending.is_empty());
}

#[test]
fn closing_a_question_keeps_a_pending_approval_flagged() {
    let (mut app, _events) = app();
    ask(&mut app, "q1", json!("child"));
    assert!(approval::on_callback_call(
        &mut app,
        &callback("approval", "a1", json!("child"))
    ));
    let label = app.view.loading.label().to_owned();

    question_app::dismiss(
        &mut app,
        &question_app::QuestionSource::Callback(CallbackAddress {
            callback_id: "q1".into(),
            session_id: Some("child".into()),
        }),
    );

    assert_eq!(app.view.loading.label(), label);
}

#[test]
fn results_prefer_the_callback_session_over_the_fallback() {
    let output = CallbackOutput::denial("approval").unwrap();
    let address = CallbackAddress {
        callback_id: "c1".into(),
        session_id: Some("child".into()),
    };
    let params = address.result_params(Some(ROOT), &output).unwrap();
    assert_eq!(params["sessionId"], "child");
    let address = CallbackAddress {
        session_id: None,
        ..address
    };
    assert_eq!(
        address.result_params(Some(ROOT), &output).unwrap()["sessionId"],
        ROOT
    );
    assert!(address.result_params(None, &output).is_none());
}

#[tokio::test]
async fn headless_denies_every_advertised_kind_at_the_session_that_asked() {
    for kind in CALLBACK_KINDS {
        let (notification_tx, _notifications) = mpsc::channel(1);
        let (writer_tx, mut writer) = mpsc::unbounded_channel();
        let pending = Pending::default();
        let active_session: ActiveSession = Arc::new(Mutex::new(Some(ROOT.to_owned())));
        let next_request_id = AtomicU64::new(1);

        callback::handle_server_request(
            1,
            server_method::CALLBACK_CALL,
            Some(callback(kind, "c1", json!("child"))),
            callback::DenyWiring {
                pending: &pending,
                notif_tx: &notification_tx,
                to_writer: &writer_tx,
                next_request_id: &next_request_id,
                active_session: &active_session,
            },
            true,
        )
        .await;

        let _ack = writer.recv().await.unwrap().expect("ack frame");
        let deny: Value =
            serde_json::from_str(&writer.recv().await.unwrap().expect("deny frame")).unwrap();
        assert_eq!(deny["params"]["sessionId"], "child", "{kind}");
        assert_eq!(deny["params"]["result"]["output"]["type"], *kind);
    }
}
