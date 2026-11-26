//! `vibe -p` against a scripted app-server: denied tool calls, answerless runs and bad agents.

use std::io::{BufRead, Write};
use std::process::Command;

use serde_json::{json, Value};

const DENIAL: &str =
    "Tool call denied: file_system.bash (needs approval, which a headless run cannot give)";
const NO_ANSWER: &str = "The agent finished without an answer.";

fn send(frame: Value) {
    println!("{frame}");
    std::io::stdout().flush().unwrap();
}

fn reply(id: &Value, result: Value) {
    send(json!({"jsonrpc": "2.0", "id": id, "result": result}));
}

fn notify(method: &str, params: Value) {
    send(json!({"jsonrpc": "2.0", "method": method, "params": params}));
}

fn denied_call(turn_id: &str) -> Value {
    json!({
        "id": format!("effect-{turn_id}"), "type": "effect", "turnId": turn_id,
        "generationStatus": "completed",
        "title": "file_system.bash", "detail": {"toolName": "file_system.bash"},
        "state": {
            "status": "failed", "approvalSource": "user",
            "error": {"code": "tool_denied", "message": "Tool execution denied by approval callback"},
        },
    })
}

fn failed_turn(code: &str, message: &str) -> Value {
    json!({"turn": {"id": "turn-1", "status": "failed", "error": {"code": code, "message": message}}})
}

/// `FAKE_SCENARIO`: after a denied call, `answerless` ends the turn with a blank answer,
/// `failed` fails it, `aborted` aborts the run and fails the interrupted turn; `typo` rejects agent `typo`.
#[test]
#[ignore]
fn fake_server_denies_a_tool_call() {
    let scenario = std::env::var("FAKE_SCENARIO").unwrap();
    for line in std::io::stdin().lock().lines() {
        let msg: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let (Some(id), Some(method)) = (msg.get("id"), msg["method"].as_str()) else {
            continue;
        };
        match method {
            "session/start" if msg.pointer("/params/agentConfig/agent") == Some(&json!("typo")) => {
                send(json!({
                    "jsonrpc": "2.0", "id": id,
                    "error": {"code": "invalid_params", "message": "Agent 'typo' not found."},
                }));
            }
            "session/start" => reply(id, json!({"state": {"session": {"id": "session-1"}}})),
            "config/effective/read" => reply(id, json!({"config": {}})),
            "turn/start" => {
                reply(id, json!({"turn": {"id": "turn-1"}}));
                notify(
                    "history/entryAdded",
                    json!({"entry": denied_call("turn-1")}),
                );
                match scenario.as_str() {
                    "aborted" => notify("agentSocket/aborted", json!({"message": "budget gone"})),
                    "failed" => notify(
                        "turn/completed",
                        failed_turn("context_too_long", "Context too long"),
                    ),
                    _ => notify(
                        "turn/completed",
                        json!({"turn": {"id": "turn-1", "status": "completed"}}),
                    ),
                }
            }
            "turn/interrupt" => {
                reply(id, json!({}));
                notify("turn/completed", failed_turn("interrupted", "interrupted"));
            }
            "session/history/list" => reply(
                id,
                json!({"items": [
                    // An earlier run's denial: that run reported it.
                    denied_call("turn-0"),
                    denied_call("turn-1"),
                    {
                        "id": "answer-1", "type": "message", "role": "assistant", "turnId": "turn-1",
                        "generationStatus": "completed", "content": [{"type": "text", "text": " "}],
                    },
                ]}),
            ),
            "session/subagents/wait" => reply(id, json!({"settled": true, "running": []})),
            "session/subagents/interrupt" => reply(id, json!({"results": []})),
            "session/subagents/read" => reply(id, json!({"subagents": []})),
            _ => reply(id, json!({})),
        }
    }
}

struct Run {
    code: Option<i32>,
    stdout: String,
    stderr: String,
    export: Value,
}

fn run(scenario: &str, args: &[&str]) -> Run {
    let dir = tempfile::tempdir().unwrap();
    let exe = std::env::current_exe().unwrap();
    let server = [
        exe.to_str().unwrap(),
        "--ignored",
        "--exact",
        &test_name!("fake_server_denies_a_tool_call"),
        "--nocapture",
    ];
    let output = Command::new(env!("CARGO_BIN_EXE_vibe-rs"))
        .args(["-p", "run the tests", "--output-dir"])
        .arg(dir.path().join("out"))
        .arg("--workdir")
        .arg(dir.path())
        .args(args)
        .env("VIBE_HOME", dir.path().join("home"))
        .env("VIBE_APP_SERVER_CMD", shlex::try_join(server).unwrap())
        .env("FAKE_SCENARIO", scenario)
        .env_remove("VIBE_APP_SERVER_BIN")
        .env_remove("VIBE_CWD")
        .output()
        .unwrap();
    let export = std::fs::read_to_string(dir.path().join("out/export.json")).unwrap();
    Run {
        code: output.status.code(),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        export: serde_json::from_str(&export).unwrap(),
    }
}

fn assert_one_denial(run: &Run) {
    assert_eq!(run.stderr.matches(DENIAL).count(), 1, "{}", run.stderr);
    assert_eq!(
        run.export["warnings"],
        json!([{"message": DENIAL, "source": "file_system.bash"}])
    );
}

#[test]
fn a_text_run_reports_the_denial_once_and_says_it_has_no_answer() {
    let run = run("answerless", &["--output", "text"]);

    assert_eq!(run.code, Some(0));
    assert_eq!(run.stdout, "");
    assert_one_denial(&run);
    assert!(run.stderr.contains(NO_ANSWER), "{}", run.stderr);
}

#[test]
fn a_streaming_run_reports_the_denial_once_and_never_says_it_has_no_answer() {
    let run = run("answerless", &["--output", "streaming"]);

    assert_eq!(run.code, Some(0));
    let entries: Vec<Value> = run
        .stdout
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(entries, vec![denied_call("turn-1")]);
    assert_one_denial(&run);
    assert!(!run.stderr.contains(NO_ANSWER), "{}", run.stderr);
}

#[test]
fn a_json_run_reports_the_denial_once_and_never_says_it_has_no_answer() {
    let run = run("answerless", &["--output", "json"]);

    assert_eq!(run.code, Some(0));
    assert_one_denial(&run);
    assert!(!run.stderr.contains(NO_ANSWER), "{}", run.stderr);
}

#[test]
fn a_failed_turn_still_reports_the_denial() {
    let run = run("failed", &["--output", "text"]);

    assert_eq!(run.export["outcome"], "length");
    assert_one_denial(&run);
}

#[test]
fn a_stopped_run_reports_the_denial_once() {
    let run = run("aborted", &["--output", "text"]);

    assert_eq!(run.export["outcome"], "aborted");
    assert_one_denial(&run);
    assert!(!run.stderr.contains(NO_ANSWER), "{}", run.stderr);
}

#[test]
fn an_unknown_agent_is_a_usage_error() {
    let run = run("typo", &["--agent", "typo"]);

    assert_eq!(run.code, Some(1));
    assert_eq!(run.export["outcome"], "usage_error");
    assert!(
        run.stderr.contains("Agent 'typo' not found."),
        "{}",
        run.stderr
    );
}
