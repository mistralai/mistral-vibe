//! Regression: an oversized frame fails its request, not the whole transport.

use std::io::{BufRead, Write};
use std::time::Duration;

use serde_json::{json, Value};
use vibe_rs::server::frames::MAX_FRAME;
use vibe_rs::server::{Client, Launch, RequestFailure};

fn print(frame: Value) {
    println!("{frame}");
    std::io::stdout().flush().unwrap();
}

#[test]
#[ignore]
fn fake_server_sends_oversized_frames() {
    let stdin = std::io::stdin();
    let mut lines = stdin.lock().lines();
    while let Some(line) = lines.next() {
        let message: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let result = match message["method"].as_str() {
            Some("session/read") => json!({"text": "x".repeat(MAX_FRAME)}),
            // Relay the client's answer to an oversized server request.
            Some("relay") => {
                let params = json!({"text": "x".repeat(MAX_FRAME)});
                print(
                    json!({"jsonrpc": "2.0", "id": 99, "method": "callback/call", "params": params}),
                );
                let reply: Value = serde_json::from_str(&lines.next().unwrap().unwrap()).unwrap();
                json!({"reply": reply})
            }
            _ => json!({"ok": true}),
        };
        print(json!({"jsonrpc": "2.0", "id": message["id"], "result": result}));
    }
}

#[tokio::test]
async fn oversized_frames_fail_only_their_request() {
    let launch = Launch {
        program: std::env::current_exe()
            .expect("test executable")
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            test_name!("fake_server_sends_oversized_frames"),
            "--nocapture".into(),
        ],
        cwd: None,
    };
    let (client, _child, _notifications, crash_rx) =
        Client::spawn(launch).await.expect("spawn backend");
    let within = |request| tokio::time::timeout(Duration::from_secs(10), request);

    let read = within(client.request("session/read", json!({})))
        .await
        .expect("session/read timed out")
        .expect_err("oversized response must fail");
    let relayed = within(client.request("relay", json!({})))
        .await
        .expect("relay timed out")
        .expect("transport still up");
    let next = within(client.request("ping", json!({})))
        .await
        .expect("follow-up request timed out")
        .expect("transport still up");

    assert!(matches!(
        read.downcast_ref::<RequestFailure>(),
        Some(RequestFailure::FrameTooLarge(len)) if *len > MAX_FRAME
    ));
    assert_eq!(relayed["reply"]["id"], 99);
    assert_eq!(relayed["reply"]["error"]["code"], "invalid_request");
    assert_eq!(next, json!({"ok": true}));
    assert!(!*crash_rx.borrow());
}
