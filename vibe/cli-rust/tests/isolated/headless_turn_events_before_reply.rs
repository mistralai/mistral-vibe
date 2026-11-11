//! Turn events that arrive before the `turn/start` reply, as on the Session
//! API path: the run keeps them and still answers with its own turn.

use std::io::{BufRead, Write};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};
use vibe_rs::cli::{HeadlessOptions, HeadlessSession, OutputFormat};
use vibe_rs::headless::drive;
use vibe_rs::headless_run::StopRequests;
use vibe_rs::run_export::{RunLimits, RunOutcome};
use vibe_rs::server::{Connect, Launch};

fn assistant(id: &str, turn_id: &str, text: &str) -> Value {
    json!({
        "id": id,
        "turnId": turn_id,
        "role": "assistant",
        "type": "message",
        "generationStatus": "completed",
        "content": [{"type": "text", "text": text}],
    })
}

fn turn(id: &str, status: &str) -> Value {
    json!({"id": id, "sessionId": "session-1", "status": status})
}

fn send(frame: Value) {
    println!("{}", serde_json::to_string(&frame).unwrap());
    std::io::stdout().flush().unwrap();
}

fn notify(method: &str, params: Value) {
    send(json!({"jsonrpc": "2.0", "method": method, "params": params}));
}

#[test]
#[ignore]
fn fake_server_streams_turn_before_reply() {
    for line in std::io::stdin().lock().lines() {
        let msg: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let Some(id) = msg.get("id") else {
            continue;
        };
        let result = match msg["method"].as_str().unwrap_or_default() {
            "session/start" => json!({"state": {"session": {"id": "session-1"}}}),
            "config/effective/read" => json!({"config": {}}),
            // A turn queued after the run's own already answered: the run must not take it.
            "session/history/list" => json!({
                "items": [
                    assistant("entry-1", "turn-1", "early answer"),
                    assistant("entry-2", "turn-2", "later answer"),
                ],
                "nextCursor": null,
            }),
            "turn/start" => {
                // The whole turn, and the start of the next, run before the reply leaves.
                notify(
                    "turn/started",
                    json!({"turn": turn("turn-1", "in_progress")}),
                );
                notify(
                    "history/entryAdded",
                    json!({"entry": assistant("entry-1", "turn-1", "early answer")}),
                );
                notify(
                    "turn/completed",
                    json!({"turn": turn("turn-1", "completed")}),
                );
                notify(
                    "turn/started",
                    json!({"turn": turn("turn-2", "in_progress")}),
                );
                notify(
                    "history/entryAdded",
                    json!({"entry": assistant("entry-2", "turn-2", "later answer")}),
                );
                json!({"turn": turn("turn-1", "in_progress")})
            }
            _ => json!({}),
        };
        send(json!({"jsonrpc": "2.0", "id": id, "result": result}));
    }
}

fn options() -> HeadlessOptions {
    HeadlessOptions {
        prompt: "say hi".to_owned(),
        session: HeadlessSession::New,
        output: OutputFormat::Silent,
        max_turns: None,
        max_price: None,
        max_tokens: None,
        time_limit: None,
        output_dir: None,
        system_prompt: None,
        model: None,
        agent: None,
        auto_approve: false,
        enabled_tools: Vec::new(),
        disabled_tools: Vec::new(),
        trust: false,
        add_dir: Vec::new(),
        worktree: None,
        agent_socket: None,
    }
}

#[tokio::test]
async fn a_turn_that_ends_before_its_reply_finishes_the_run_with_its_answer() {
    let launch = Launch {
        program: std::env::current_exe()
            .expect("test executable")
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            test_name!("fake_server_streams_turn_before_reply"),
            "--nocapture".into(),
        ],
        cwd: None,
    };
    let stop = Arc::new(StopRequests::new(RunLimits::default()));
    let report = tokio::time::timeout(
        Duration::from_secs(20),
        drive(&options(), None, Connect::Spawn(launch), &stop),
    )
    .await
    .expect("the run waited for a turn/completed it already received");

    assert_eq!(report.result.outcome, RunOutcome::Finished);
    assert!(report.result.stop_reason.is_none());
    assert_eq!(report.final_response.as_deref(), Some("early answer"));
}
