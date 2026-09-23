//! Callback kinds stay advertised and require a semantic answer from the UI.

use std::collections::HashMap;
use std::sync::atomic::AtomicU64;
use std::sync::{Arc, Mutex};

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status, ToastSeverity};
use vibe_rs::approval::{self, Event as ApprovalEvent, KeyAction, State, MAX_PENDING_APPROVALS};
use vibe_rs::server::{
    callback, server_method, ActiveSession, ApprovalCallback, ApprovalDecisionType, Pending,
    CALLBACK_KINDS,
};

fn approval_callback(callback_id: &str) -> ApprovalCallback {
    serde_json::from_value(json!({
        "callbackId": callback_id,
        "sessionId": "session-child",
        "detail": {"kind": "approval"},
    }))
    .unwrap()
}

#[test]
fn user_input_callbacks_are_advertised() {
    assert!(CALLBACK_KINDS.contains(&"user_input"));
    assert!(CALLBACK_KINDS.contains(&"approval"));
}

#[test]
fn approval_decisions_use_the_public_wire_values() {
    let decisions = [
        ApprovalDecisionType::Approve,
        ApprovalDecisionType::ApproveForSession,
        ApprovalDecisionType::ApprovePermanently,
        ApprovalDecisionType::Deny,
    ];
    assert_eq!(
        serde_json::to_value(decisions).unwrap(),
        json!([
            "approve",
            "approve_for_session",
            "approve_permanently",
            "deny"
        ])
    );
}

#[test]
fn approval_callbacks_tolerate_older_backend_shapes() {
    let callback: ApprovalCallback = serde_json::from_value(json!({
        "callbackId": "approval-1",
        "detail": {"kind": "approval"},
    }))
    .unwrap();

    assert_eq!(callback.callback_id, "approval-1");
    assert_eq!(callback.session_id, "");
    assert_eq!(callback.detail.effect.tool_name, "");
    assert!(callback.detail.effect.input.is_null());
}

#[test]
fn approval_queue_deduplicates_and_has_a_declared_bound() {
    let mut state = State::default();
    for index in 0..MAX_PENDING_APPROVALS {
        assert!(matches!(
            state.enqueue(approval_callback(&format!("approval-{index}"))),
            Ok(true)
        ));
    }
    assert_eq!(state.pending_len(), MAX_PENDING_APPROVALS);
    assert!(matches!(
        state.enqueue(approval_callback("approval-0")),
        Ok(false)
    ));
    assert!(state
        .enqueue(approval_callback("approval-overflow"))
        .is_err());
    assert!(!state.has_capacity());
}

#[test]
fn full_approval_queue_defers_delivery() {
    let mut app = App::default();
    for index in 0..MAX_PENDING_APPROVALS {
        app.approval
            .enqueue(approval_callback(&format!("approval-{index}")))
            .unwrap();
    }
    let params = json!({
        "callback": {
            "callbackId": "approval-deferred",
            "sessionId": "session-child",
            "detail": {"kind": "approval"},
        }
    });

    assert!(!approval::on_callback_call(&mut app, &params));
    assert_eq!(app.approval.pending_len(), MAX_PENDING_APPROVALS);

    app.session.status = Status::Ready;
    approval::show_pending(&mut app);
    assert!(approval::on_callback_call(&mut app, &params));
    assert_eq!(app.approval.pending_len(), MAX_PENDING_APPROVALS);
}

#[test]
fn approval_waits_for_startup_readiness() {
    let mut app = App::default();
    app.approval
        .enqueue(approval_callback("approval-1"))
        .unwrap();

    assert!(!approval::can_wake(&app));
    approval::show_pending(&mut app);
    assert!(!app.approval.open);
    assert_eq!(app.approval.pending_len(), 1);

    app.session.status = Status::Ready;
    assert!(approval::can_wake(&app));
    approval::show_pending(&mut app);
    assert!(app.approval.open);
    assert_eq!(app.approval.pending_len(), 0);
    assert_eq!(
        app.approval
            .active
            .as_ref()
            .map(|item| item.session_id.as_str()),
        Some("session-child")
    );
}

#[test]
fn approval_only_advances_after_a_successful_response() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    app.approval
        .enqueue(approval_callback("approval-1"))
        .unwrap();
    app.approval
        .enqueue(approval_callback("approval-2"))
        .unwrap();
    approval::show_pending(&mut app);

    approval::apply_event(
        &mut app,
        ApprovalEvent::Failed {
            callback_id: "approval-1".into(),
            error: "backend unavailable".into(),
        },
    );
    assert!(app.approval.open);
    assert_eq!(
        app.approval
            .active
            .as_ref()
            .map(|callback| callback.callback_id.as_str()),
        Some("approval-1")
    );

    approval::apply_event(
        &mut app,
        ApprovalEvent::Responded {
            callback_id: "approval-1".into(),
        },
    );
    assert!(app.approval.open);
    assert_eq!(
        app.approval
            .active
            .as_ref()
            .map(|callback| callback.callback_id.as_str()),
        Some("approval-2")
    );
}

#[test]
fn answering_approval_keeps_unrelated_toasts() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    app.approval
        .enqueue(approval_callback("approval-1"))
        .unwrap();
    approval::show_pending(&mut app);
    app.approval.mount_time = None;
    app.show_toast("server warning".into(), ToastSeverity::Warning, 5);
    let client = Arc::new(vibe_rs::server::Client::stub());

    approval::handle_key(
        &mut app,
        &client,
        KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
    );
    assert_eq!(app.overlays.toasts.len(), 1);

    approval::apply_event(
        &mut app,
        ApprovalEvent::Responded {
            callback_id: "approval-1".into(),
        },
    );
    assert_eq!(app.overlays.toasts.len(), 1);
}

#[test]
fn approval_character_shortcuts_require_no_modifiers() {
    let enter = KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE);
    let bare_y = KeyEvent::new(KeyCode::Char('y'), KeyModifiers::NONE);
    let ctrl_y = KeyEvent::new(KeyCode::Char('y'), KeyModifiers::CONTROL);
    let alt_three = KeyEvent::new(KeyCode::Char('3'), KeyModifiers::ALT);

    assert_eq!(approval::key_action(&enter), Some(KeyAction::Submit));
    assert_eq!(approval::key_action(&bare_y), Some(KeyAction::Choose(0)));
    assert_eq!(approval::key_action(&ctrl_y), None);
    assert_eq!(approval::key_action(&alt_three), None);
}

#[tokio::test]
async fn approval_callback_is_acknowledged_and_forwarded_without_auto_approval() {
    // The forwarded notification is the return value here (not sent through
    // `notif_tx`), so the receiver is unused; the sender still wires `DenyWiring`.
    let (notification_tx, _notifications) = mpsc::channel(1);
    let (writer_tx, mut writer) = mpsc::unbounded_channel();
    let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
    let active_session: ActiveSession = Arc::new(Mutex::new(None));
    let next_request_id = AtomicU64::new(1);
    let callback = json!({
        "callback": {
            "callbackId": "approval-1",
            "sessionId": "session-1",
            "detail": {"kind": "approval"},
        }
    });

    let forwarded = callback::handle_server_request(
        42,
        server_method::CALLBACK_CALL,
        Some(callback.clone()),
        callback::DenyWiring {
            pending: &pending,
            notif_tx: &notification_tx,
            to_writer: &writer_tx,
            next_request_id: &next_request_id,
            active_session: &active_session,
        },
        false,
    )
    .await
    .expect("callback notification");

    let acknowledgement: Value =
        serde_json::from_str(&writer.recv().await.unwrap().expect("ack frame")).unwrap();
    assert_eq!(
        acknowledgement,
        json!({
            "jsonrpc": "2.0",
            "id": 42,
            "result": {"callbackId": "approval-1", "accepted": true},
        })
    );
    assert!(writer.try_recv().is_err());
    assert_eq!(forwarded.method, server_method::CALLBACK_CALL);
    assert_eq!(forwarded.params, callback);
}

#[tokio::test]
async fn denial_without_session_id_falls_back_to_the_active_session() {
    let (notification_tx, _notifications) = mpsc::channel(1);
    let (writer_tx, mut writer) = mpsc::unbounded_channel();
    let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
    let active_session: ActiveSession = Arc::new(Mutex::new(Some("session-live".to_owned())));
    let next_request_id = AtomicU64::new(1);
    // Tolerated older backend shape: no `sessionId` (ADR 0014).
    let callback = json!({
        "callback": {
            "callbackId": "approval-2",
            "detail": {"kind": "approval"},
        }
    });

    callback::handle_server_request(
        7,
        server_method::CALLBACK_CALL,
        Some(callback),
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

    let _ack: Value =
        serde_json::from_str(&writer.recv().await.unwrap().expect("ack frame")).unwrap();
    let deny: Value =
        serde_json::from_str(&writer.recv().await.unwrap().expect("deny frame")).unwrap();
    assert_eq!(deny["params"]["sessionId"], "session-live");
    assert_eq!(deny["params"]["result"]["callbackId"], "approval-2");
    assert_eq!(
        deny["params"]["result"]["output"]["decision"]["type"],
        "deny"
    );
    // The deny result is registered so a rejection is delivered, not dropped.
    let deny_id = deny["id"].as_u64().unwrap();
    assert!(pending.lock().unwrap().contains_key(&deny_id));
}
