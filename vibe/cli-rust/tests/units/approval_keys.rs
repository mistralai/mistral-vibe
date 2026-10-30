//! Approval keys: j/k wrap like the arrows, and the mount grace period swallows decisions only.

use std::time::{Duration, Instant};

use crossterm::event::KeyCode;
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::approval;
use vibe_rs::server::ApprovalCallback;

use crate::support::{key, stub, with_status};

fn open_approval() -> (App, mpsc::Receiver<vibe_rs::ui_event::UiEvent>) {
    let mut app = with_status(Status::Ready);
    let (tx, rx) = mpsc::channel(4);
    app.events = Some(tx);
    let callback: ApprovalCallback = serde_json::from_value(json!({
        "callbackId": "approval-1",
        "sessionId": "session-1",
        "detail": {"kind": "approval"},
    }))
    .unwrap();
    app.approval.enqueue(callback).unwrap();
    approval::show_pending(&mut app);
    assert!(app.approval.open);
    (app, rx)
}

fn press(app: &mut App, code: KeyCode) {
    approval::handle_key(app, &stub(), key(code));
}

#[tokio::test]
async fn j_and_k_move_like_the_arrows_and_wrap() {
    let (mut app, _answers) = open_approval();
    app.approval.mount_time = None;

    press(&mut app, KeyCode::Char('j'));
    assert_eq!(app.approval.list.selected(), 1);
    press(&mut app, KeyCode::Char('k'));
    assert_eq!(app.approval.list.selected(), 0);
    press(&mut app, KeyCode::Char('k'));
    assert_eq!(app.approval.list.selected(), 3, "k wraps to Deny");
    press(&mut app, KeyCode::Down);
    assert_eq!(app.approval.list.selected(), 0, "Down wraps to Allow once");
}

#[tokio::test]
async fn decisions_are_swallowed_within_the_grace_period_but_navigation_works() {
    let (mut app, mut answers) = open_approval();
    app.approval.mount_time = Some(Instant::now());

    for code in [
        KeyCode::Char('y'),
        KeyCode::Char('n'),
        KeyCode::Char('1'),
        KeyCode::Enter,
        KeyCode::Esc,
    ] {
        press(&mut app, code);
    }
    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Char('j'));

    assert_eq!(app.approval.list.selected(), 2);
    assert!(app.approval.active.is_some());
    assert!(
        tokio::time::timeout(Duration::from_millis(50), answers.recv())
            .await
            .is_err(),
        "no decision was sent"
    );

    app.approval.mount_time = None;
    press(&mut app, KeyCode::Enter);
    assert!(
        answers.recv().await.is_some(),
        "Enter answers once the grace ends"
    );
}
