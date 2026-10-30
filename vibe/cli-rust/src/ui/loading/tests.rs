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
        .map(|callback| callback.address.callback_id.as_str())
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

#[test]
fn a_long_startup_error_wraps_so_its_closing_hint_is_visible() {
    let mut app = App::default();
    app.session.status = Status::Failed;
    app.session.startup_error = Some(
        "Invalid configuration: Agent 'slow' in /home/user/.vibe/agents/slow.toml: tools: \
         tools.bash: default_timeout is set without max_timeout_seconds. Set both, or neither."
            .into(),
    );
    let mut terminal = ratatui::Terminal::new(ratatui::backend::TestBackend::new(80, 24)).unwrap();
    terminal.draw(|frame| app.draw(frame)).unwrap();
    let buffer = terminal.backend().buffer();
    let screen: String = (0..buffer.area.height)
        .map(|y| {
            (0..buffer.area.width)
                .map(|x| buffer[(x, y)].symbol())
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n");
    assert!(screen.contains("Failed to initialize: Invalid configuration"));
    assert!(screen.contains("Set both, or neither."), "{screen}");
}
