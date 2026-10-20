//! The headless run's `session/subagents/*` calls against a scripted app-server.

use std::io::{BufRead, Write};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio::time::Instant;
use vibe_rs::headless::{read_subagents, stop_subagents, wait_for_subagents};
use vibe_rs::headless_run::StopRequests;
use vibe_rs::run_export::{RunLimits, RunOutcome, RunSubagentStatus};
use vibe_rs::server::{ChildHandle, Client, Launch, Notification};

fn send(frame: Value) {
    println!("{frame}");
    std::io::stdout().flush().unwrap();
}

fn reply(id: &Value, result: Value) {
    send(json!({"jsonrpc": "2.0", "id": id, "result": result}));
}

#[test]
#[ignore]
fn fake_server_scripts_subagents() {
    let mut seen: Vec<Value> = Vec::new();
    let mut waits = 0;
    for line in std::io::stdin().lock().lines() {
        let msg: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let (Some(id), Some(method)) = (msg.get("id"), msg["method"].as_str()) else {
            continue;
        };
        let params = msg.get("params").cloned().unwrap_or(Value::Null);
        let session = params["sessionId"].as_str().unwrap_or_default().to_owned();
        if method == "test/requests" {
            reply(id, json!(seen));
            continue;
        }
        seen.push(json!({"method": method, "params": params}));
        match (method, session.as_str()) {
            (_, "old-server") => send(json!({
                "jsonrpc": "2.0", "id": id,
                "error": {"code": "method_not_found", "message": format!("Unknown method: {method}")},
            })),
            (_, "silent") => {}
            ("session/subagents/wait", _) => {
                waits += 1;
                if waits == 1 {
                    send(
                        json!({"jsonrpc": "2.0", "method": "agentSocket/aborted", "params": {"message": "budget gone"}}),
                    );
                }
                reply(id, json!({"settled": waits > 1, "running": []}));
            }
            ("session/subagents/interrupt", _) => reply(
                id,
                json!({"results": [
                    {"sessionId": "c1", "name": "reader", "outcome": "interrupted"},
                    {"sessionId": "c2", "name": "writer", "outcome": "failed", "error": "boom"},
                ]}),
            ),
            ("session/subagents/read", _) => reply(
                id,
                json!({"subagents": [{
                    "sessionId": "c1", "parentSessionId": "root", "rootSessionId": "root",
                    "name": "reader", "spawnActionId": "a1", "status": "interrupted",
                    "sessionDir": "/stores/c1",
                    "usage": {"inputTokens": 1000, "outputTokens": 10, "cachedInputTokens": 400, "totalTokens": 1010},
                    "costUsd": 0.00146, "steps": 3,
                }]}),
            ),
            _ => reply(id, Value::Null),
        }
    }
}

async fn scripted_server() -> (Arc<Client>, mpsc::Receiver<Notification>, ChildHandle) {
    let launch = Launch {
        program: std::env::current_exe()
            .expect("test executable")
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            "fake_server_scripts_subagents".into(),
            "--nocapture".into(),
        ],
        cwd: None,
    };
    let (client, child, notifications, _crash_rx) =
        Client::spawn(launch).await.expect("spawn backend");
    (Arc::new(client), notifications, child)
}

async fn requests(client: &Client) -> Vec<Value> {
    let seen = client.request("test/requests", json!({})).await.unwrap();
    seen.as_array().unwrap().clone()
}

#[tokio::test]
async fn the_run_waits_until_its_subagents_settle_and_still_takes_an_abort() {
    let (client, mut notifications, _child) = scripted_server().await;
    let stop = StopRequests::new(RunLimits::default());

    tokio::time::timeout(
        Duration::from_secs(10),
        wait_for_subagents(&client, &mut notifications, "root", &stop),
    )
    .await
    .expect("the wait settles");

    let seen = requests(&client).await;
    assert_eq!(seen.len(), 2);
    for request in &seen {
        assert_eq!(request["method"], "session/subagents/wait");
        assert_eq!(
            request["params"],
            json!({"sessionId": "root", "timeoutMs": 60_000})
        );
    }
    assert_eq!(stop.requested_outcome(), Some(RunOutcome::Aborted));
    assert_eq!(stop.abort_error().unwrap().message, "budget gone");
}

#[tokio::test]
async fn a_server_without_the_methods_does_not_hold_the_run() {
    let (client, mut notifications, _child) = scripted_server().await;
    let stop = StopRequests::new(RunLimits::default());
    let ends = Instant::now() + Duration::from_secs(5);

    tokio::time::timeout(
        Duration::from_secs(10),
        wait_for_subagents(&client, &mut notifications, "old-server", &stop),
    )
    .await
    .expect("the wait gives up");
    let results = stop_subagents(&client, "old-server", "the run stopped", ends).await;
    let subagents = read_subagents(&client, "old-server", ends).await;

    assert!(results.is_empty());
    assert!(subagents.is_empty());
    assert_eq!(stop.requested_outcome(), None);
}

#[tokio::test]
async fn stopping_interrupts_each_subagent_then_waits_within_the_stop() {
    let (client, _notifications, _child) = scripted_server().await;
    let ends = Instant::now() + Duration::from_secs(7);

    let results = stop_subagents(&client, "root", "the run stopped: deadline", ends).await;

    let outcomes: Vec<_> = results
        .iter()
        .map(|r| (r.name.as_str(), r.outcome.as_str(), r.error.as_deref()))
        .collect();
    assert_eq!(
        outcomes,
        vec![
            ("reader", "interrupted", None),
            ("writer", "failed", Some("boom")),
        ]
    );
    let seen = requests(&client).await;
    assert_eq!(seen[0]["method"], "session/subagents/interrupt");
    assert_eq!(
        seen[0]["params"],
        json!({"sessionId": "root", "reason": "the run stopped: deadline"})
    );
    assert_eq!(seen[1]["method"], "session/subagents/wait");
    let timeout_ms = seen[1]["params"]["timeoutMs"].as_u64().unwrap();
    assert!((6_000..=7_000).contains(&timeout_ms), "{timeout_ms}");
}

#[tokio::test]
async fn the_report_reads_the_subagents_leaving_time_to_stop_the_session() {
    let (client, _notifications, _child) = scripted_server().await;
    let ends = Instant::now() + Duration::from_secs(10);

    let subagents = read_subagents(&client, "root", ends).await;

    let [report] = subagents.as_slice() else {
        panic!("one subagent: {subagents:?}");
    };
    assert_eq!(report.subagent.agent_name, "reader");
    assert_eq!(report.subagent.status, RunSubagentStatus::Interrupted);
    assert_eq!(report.subagent.cost_usd, Some(0.00146));
    assert_eq!(
        report.subagent.usage.as_ref().unwrap().cached_input_tokens,
        400
    );
    assert_eq!(report.session_dir.as_deref(), Some("/stores/c1".as_ref()));
    let seen = requests(&client).await;
    assert_eq!(seen[0]["method"], "session/subagents/read");
    let timeout_ms = seen[0]["params"]["timeoutMs"].as_u64().unwrap();
    assert!((8_000..=9_000).contains(&timeout_ms), "{timeout_ms}");
}

#[tokio::test]
async fn a_server_that_does_not_answer_costs_the_report_no_more_than_its_grace() {
    let (client, _notifications, _child) = scripted_server().await;
    let started = Instant::now();
    let ends = started + Duration::from_millis(500);

    let results = stop_subagents(&client, "silent", "the run stopped", ends).await;
    let subagents = read_subagents(&client, "silent", ends).await;

    assert!(results.is_empty());
    assert!(subagents.is_empty());
    assert!(started.elapsed() < Duration::from_secs(3));
}
