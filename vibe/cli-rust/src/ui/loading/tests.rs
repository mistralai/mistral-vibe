use std::time::Instant;

use serde_json::json;

use super::SHELL_LOADING_STATUS;
use crate::app::{App, Status};
use crate::approval::{self, Event};
use crate::server::ApprovalCallback;

fn approval(callback_id: &str, status: &str) -> ApprovalCallback {
    serde_json::from_value(json!({
        "callbackId": callback_id,
        "sessionId": "session",
        "detail": {"kind": "approval", "effect": {"display": {"statusText": status}}},
    }))
    .unwrap()
}

fn active_id(app: &App) -> Option<&str> {
    app.approval
        .active
        .as_ref()
        .map(|callback| callback.callback_id.as_str())
}

#[test]
fn answering_one_of_two_approvals_keeps_the_loader_paused_on_approval_status() {
    let mut app = App::default();
    app.session.status = Status::Generating {
        since: Instant::now(),
    };
    app.view.loading.set_label(SHELL_LOADING_STATUS);
    app.approval
        .enqueue(approval(
            "approval-1",
            "Waiting for approval to run example",
        ))
        .unwrap();
    approval::show_pending(&mut app);
    app.approval
        .enqueue(approval(
            "approval-2",
            "Waiting for approval to run example",
        ))
        .unwrap();
    approval::show_pending(&mut app);
    assert_eq!(active_id(&app), Some("approval-1"));
    assert!(app.approval.has_pending());
    assert_eq!(
        app.view.loading.label(),
        "Waiting for approval to run example"
    );
    let paused_at = app.view.loading.pause_start.expect("the timer is paused");

    approval::apply_event(
        &mut app,
        Event::Responded {
            callback_id: "approval-1".into(),
        },
    );
    assert_eq!(active_id(&app), Some("approval-2"));
    assert!(!app.approval.has_pending());
    assert_eq!(
        app.view.loading.label(),
        "Waiting for approval to run example"
    );
    assert_eq!(app.view.loading.pause_start, Some(paused_at));

    approval::apply_event(
        &mut app,
        Event::Responded {
            callback_id: "approval-2".into(),
        },
    );
    assert!(app.approval.active.is_none());
    assert_eq!(app.view.loading.label(), SHELL_LOADING_STATUS);
    assert!(app.view.loading.pause_start.is_none());
}
