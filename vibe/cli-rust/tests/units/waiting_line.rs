//! A turn that only waits: its wait is hidden, its group reads as done, and a still grey line names what it waits for.

use std::sync::Arc;

use crossterm::event::KeyCode;
use serde_json::{json, Value};
use vibe_rs::app::{App, Status};
use vibe_rs::input::handle_key;
use vibe_rs::server::{notification, Client};
use vibe_rs::ui;

use crate::support::{key, notify, render, rows};

fn wait_entry(status: &str) -> Value {
    json!({
        "id": "wait", "type": "effect", "turnId": "turn",
        "generationStatus": if status == "running" { "in_progress" } else { "completed" },
        "detail": {"toolName": "self.wait", "kind": "tool"},
        "state": {"status": status},
    })
}

fn tool_entry(id: &str) -> Value {
    json!({
        "id": id, "type": "effect", "turnId": "turn", "generationStatus": "completed",
        "detail": {"toolName": "agents.spawn", "kind": "subagent"},
        "state": {"status": "completed"},
    })
}

fn waiting_app(history: Vec<Value>, children: Value, processes: Value) -> App {
    let mut app = App::default();
    app.session.session_id = Some("session".into());
    app.session.active_turn_id = Some("turn".into());
    app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });
    notify(
        &mut app,
        notification::SESSION_SNAPSHOT,
        json!({"sessionId": "session", "state": {
            "eventId": 2,
            "session": {"id": "session"},
            "history": history,
            "childSessions": children,
            "backgroundProcesses": processes,
        }}),
    );
    app
}

fn child(id: &str, status: &str) -> Value {
    json!({"id": id, "name": id, "agentType": "generic", "status": {"type": status},
           "createdAt": 1, "updatedAt": 1})
}

fn process(id: &str, status: &str) -> Value {
    json!({"processId": id, "command": "make test", "status": status, "createdAt": "now"})
}

fn screen(app: &mut App) -> String {
    rows(&render(140, 20, |frame| ui::draw(app, frame))).join("\n")
}

#[test]
fn a_wait_names_the_subagents_and_background_tasks_still_running() {
    let cases = [
        (
            json!([
                child("a", "running"),
                child("b", "blocked"),
                child("c", "idle")
            ]),
            json!([]),
            "◷ Waiting for 2 subagents…",
        ),
        (
            json!([]),
            json!([process("p", "running"), process("q", "completed")]),
            "◷ Waiting for 1 background task…",
        ),
        (
            json!([child("a", "running")]),
            json!([process("p", "running"), process("q", "running")]),
            "◷ Waiting for 1 subagent and 2 background tasks…",
        ),
    ];
    for (children, processes, expected) in cases {
        let mut app = waiting_app(vec![wait_entry("running")], children, processes);
        let screen = screen(&mut app);
        assert!(screen.contains(expected), "{expected}\n{screen}");
        assert!(screen.contains("Esc/Ctrl+C to interrupt"), "{screen}");
        assert!(screen.contains("send a message to steer"), "{screen}");
    }
}

#[test]
fn the_wait_is_hidden_and_its_group_reads_as_done() {
    let mut app = waiting_app(
        vec![tool_entry("s1"), tool_entry("s2"), wait_entry("running")],
        json!([child("a", "running")]),
        json!([]),
    );
    let group = app.view.transcript.group_of("s1").expect("groups");
    assert!(group.finalized);
    assert_eq!(group.label(), "Ran 2 subagents");
    let waiting = screen(&mut app);
    assert!(
        !waiting.contains("Waiting for background work"),
        "{waiting}"
    );
    assert!(waiting.contains("◷ Waiting for 1 subagent…"), "{waiting}");
}

#[test]
fn a_subagent_view_keeps_its_running_wait() {
    let mut child = vibe_rs::transcript::Transcript::default();
    for entry in [tool_entry("s1"), wait_entry("running")] {
        child.add(&json!({"entry": entry}));
    }
    // Only the main view has the waiting line: a subagent's wait stays a row.
    let ids: Vec<String> = child.lines().map(|entry| entry.id.to_owned()).collect();
    assert!(ids.iter().any(|id| id == "wait"), "{ids:?}");
    let group = child.group_of("s1").expect("groups");
    assert!(!group.finalized);
    assert_eq!(group.label(), "Waiting for background work, ran 1 subagent");
}

#[tokio::test]
async fn esc_interrupts_a_turn_that_only_waits() {
    let mut app = waiting_app(vec![wait_entry("running")], json!([]), json!([]));
    let (client, _frames) = Client::stub_answering();
    let client = Arc::new(client);
    let (config_tx, _config_rx) = tokio::sync::mpsc::channel(1);

    handle_key(&mut app, &client, &config_tx, key(KeyCode::Esc));

    assert!(matches!(app.session.status, Status::Ready));
}

#[test]
fn a_wait_an_earlier_turn_left_unsettled_is_not_live() {
    let mut stale = wait_entry("running");
    stale["turnId"] = json!("earlier");
    let mut app = waiting_app(
        vec![stale, tool_entry("s1"), tool_entry("s2")],
        json!([child("a", "running")]),
        json!([]),
    );
    let screen = screen(&mut app);
    // The stale wait paints like any call, and the line is not a waiting one.
    assert!(!app.view.transcript.only_model_waiting("turn"));
    assert!(!screen.contains("◷ Waiting for"), "{screen}");
}

#[test]
fn a_notice_outside_any_turn_keeps_the_wait_live() {
    let notice = json!({
        "id": "n1", "type": "notice", "level": "info", "message": "Session title updated",
        "turnId": null,
    });
    let mut app = waiting_app(
        vec![
            tool_entry("s1"),
            tool_entry("s2"),
            wait_entry("running"),
            notice,
        ],
        json!([child("a", "running")]),
        json!([]),
    );
    let group = app.view.transcript.group_of("s1").expect("groups");
    assert!(group.finalized);
    assert_eq!(group.label(), "Ran 2 subagents");
    let screen = screen(&mut app);
    assert!(!screen.contains("background work"), "{screen}");
    assert!(screen.contains("◷ Waiting for 1 subagent…"), "{screen}");
}

#[test]
fn a_session_switch_drops_the_old_background_counts() {
    let mut app = waiting_app(
        vec![wait_entry("running")],
        json!([]),
        json!([process("p", "running")]),
    );
    assert_eq!(app.session.running_processes, 1);
    app.set_session_id("another".into());
    assert_eq!(app.session.running_processes, 0);
    assert_eq!(app.session.wait_target, None);
}
