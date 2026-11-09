//! A failed history read after a completed turn: streaming already
//! delivered its result as entries arrived and discards the history, so the
//! run still finishes; JSON cannot deliver its result, so the run fails as
//! an infrastructure failure.

use std::io::{BufRead, Write};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};
use vibe_rs::cli::{HeadlessOptions, HeadlessSession, OutputFormat};
use vibe_rs::headless_run::{iso_now, HeadlessRun, StopRequests};
use vibe_rs::run_export::{RunLimits, EXPORT_FILENAME};
use vibe_rs::server::Launch;

#[test]
#[ignore]
fn fake_server_fails_history_read() {
    for line in std::io::stdin().lock().lines() {
        let msg: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let Some(id) = msg.get("id") else {
            continue;
        };
        let method = msg["method"].as_str().unwrap_or_default();
        let reply = match method {
            // The failure under test: the turn completed, but the history
            // cannot be read back.
            "session/history/list" => json!({
                "jsonrpc": "2.0",
                "id": id,
                "error": {"code": -32000, "message": "history unavailable"},
            }),
            "session/start" => json!({
                "jsonrpc": "2.0",
                "id": id,
                "result": {"state": {"session": {"id": "session-1"}}},
            }),
            "config/effective/read" => json!({
                "jsonrpc": "2.0",
                "id": id,
                "result": {"config": {}},
            }),
            "turn/start" => {
                let reply = json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "result": {"turn": {"id": "turn-1"}},
                });
                println!("{}", serde_json::to_string(&reply).unwrap());
                // One completed assistant entry, then the turn ends.
                let entry_added = json!({
                    "jsonrpc": "2.0",
                    "method": "history/entryAdded",
                    "params": {"entry": {
                        "id": "entry-1",
                        "role": "assistant",
                        "type": "message",
                        "generationStatus": "completed",
                        "content": [{"type": "text", "text": "hi"}],
                    }},
                });
                println!("{}", serde_json::to_string(&entry_added).unwrap());
                let completed = json!({
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {"turn": {"id": "turn-1", "status": "completed"}},
                });
                println!("{}", serde_json::to_string(&completed).unwrap());
                std::io::stdout().flush().unwrap();
                continue;
            }
            _ => json!({
                "jsonrpc": "2.0",
                "id": id,
                "result": {},
            }),
        };
        println!("{}", serde_json::to_string(&reply).unwrap());
        std::io::stdout().flush().unwrap();
    }
}

fn fake_server_launch() -> Launch {
    Launch {
        program: std::env::current_exe()
            .expect("test executable")
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            test_name!("fake_server_fails_history_read"),
            "--nocapture".into(),
        ],
        cwd: None,
    }
}

fn headless_options(output: OutputFormat, output_dir: &std::path::Path) -> HeadlessOptions {
    HeadlessOptions {
        prompt: "say hi".to_owned(),
        session: HeadlessSession::New,
        output,
        max_turns: None,
        max_price: None,
        max_tokens: None,
        time_limit: None,
        output_dir: Some(output_dir.to_path_buf()),
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

async fn run_against_failing_history(output: OutputFormat) -> (std::process::ExitCode, Value) {
    let output_dir = tempfile::tempdir().unwrap();
    let limits = RunLimits {
        max_turns: None,
        max_price: None,
        max_tokens: None,
        time_limit_s: None,
    };
    let headless = HeadlessRun::new(
        Some(output_dir.path().to_path_buf()),
        Arc::new(StopRequests::new(limits)),
        iso_now(),
    );
    let code = tokio::time::timeout(
        Duration::from_secs(30),
        vibe_rs::headless::run(
            headless_options(output, output_dir.path()),
            None,
            fake_server_launch(),
            headless,
        ),
    )
    .await
    .expect("headless run timed out");
    let export: Value = serde_json::from_str(
        &std::fs::read_to_string(output_dir.path().join(EXPORT_FILENAME)).expect("export written"),
    )
    .expect("export parses");
    (code, export)
}

#[tokio::test]
async fn streaming_run_finishes_when_history_read_fails() {
    let (code, export) = run_against_failing_history(OutputFormat::Streaming).await;
    assert_eq!(code, std::process::ExitCode::SUCCESS);
    assert_eq!(export["outcome"], "finished");
}

#[tokio::test]
async fn json_run_fails_when_history_read_fails() {
    let (code, export) = run_against_failing_history(OutputFormat::Json).await;
    assert_eq!(code, std::process::ExitCode::from(2));
    assert_eq!(export["outcome"], "infrastructure_failure");
}
