//! A goal that stops mid-turn notifies once, even when a snapshot of the same turn follows.

use serde_json::{json, Value};
use vibe_rs::app::{App, Status};
use vibe_rs::program_status::ProgramState;
use vibe_rs::server::notification;

use crate::support::notify;

fn goal(status: &str) -> Value {
    json!({
        "goalId": "goal-1", "revision": 3, "objective": "Ship it", "status": status,
        "tokenBudget": 50000, "tokensUsed": 12500, "timeUsedSeconds": 240,
    })
}

fn snapshot(app: &mut App, status: &str, turn: &str) {
    notify(
        app,
        notification::SESSION_SNAPSHOT,
        json!({"sessionId": "s", "state": {
            "eventId": 2,
            "session": {"id": "s", "goal": goal(status)},
            "turns": [{"id": turn, "status": "in_progress"}],
        }}),
    );
}

fn unfocused_app() -> App {
    let mut app = App::default();
    app.set_session_id("s".into());
    app.set_status(Status::Ready);
    app.terminal_notifier.configure(true, true);
    app.terminal_notifier.set_focus(false);
    app.terminal_notifier.set_default_title("Vibe");
    app
}

#[test]
fn a_snapshot_of_the_same_turn_keeps_the_goal_notification() {
    let mut app = unfocused_app();
    snapshot(&mut app, "active", "t-1");
    notify(
        &mut app,
        notification::TURN_STARTED,
        json!({"turn": {"id": "t-1"}}),
    );
    notify(
        &mut app,
        notification::SESSION_UPDATED,
        json!({"sessionId": "s", "patch": [
            {"op": "replace", "path": "/goal/status", "value": "complete"},
        ]}),
    );
    assert!(vibe_rs::goal::quiet_turn_end(&app));
    snapshot(&mut app, "complete", "t-1");
    assert!(
        vibe_rs::goal::quiet_turn_end(&app),
        "the same turn stays notified"
    );
    notify(
        &mut app,
        notification::TURN_COMPLETED,
        json!({"turn": {"id": "t-1", "status": "completed"}}),
    );
    let title = app.terminal_notifier.title(ProgramState::Done);
    assert_eq!(title, "Vibe - Goal complete");
}

#[test]
fn a_snapshot_of_another_turn_lets_its_end_notify() {
    let mut app = unfocused_app();
    snapshot(&mut app, "active", "t-1");
    notify(
        &mut app,
        notification::SESSION_UPDATED,
        json!({"sessionId": "s", "patch": [
            {"op": "replace", "path": "/goal/status", "value": "complete"},
        ]}),
    );
    snapshot(&mut app, "complete", "t-2");
    assert!(!vibe_rs::goal::quiet_turn_end(&app));
}
