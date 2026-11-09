//! Regression: app-server EOF must wake notification consumers.

use std::io::{BufRead, Write};
use std::time::Duration;

use serde_json::json;
use vibe_rs::server::{notification, Client, Launch};

#[test]
#[ignore]
fn fake_server_exits_after_turn_start() {
    for line in std::io::stdin().lock().lines() {
        let message: serde_json::Value = serde_json::from_str(&line.unwrap()).unwrap();
        if message.get("method").and_then(serde_json::Value::as_str) != Some("turn/start") {
            continue;
        }
        println!(
            "{}",
            json!({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"turn": {"id": "turn-1"}}
            })
        );
        std::io::stdout().flush().unwrap();
        std::process::exit(0);
    }
}

fn exiting_server() -> Launch {
    Launch {
        program: std::env::current_exe()
            .expect("test executable")
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            test_name!("fake_server_exits_after_turn_start"),
            "--nocapture".into(),
        ],
        cwd: None,
    }
}

#[tokio::test]
async fn app_server_exit_mid_turn_emits_disconnect_notification() {
    let (client, _child, mut notifications, _crash_rx) = Client::spawn(exiting_server())
        .await
        .expect("spawn backend");
    client
        .request("turn/start", json!({}))
        .await
        .expect("turn/start response");

    let notification = tokio::time::timeout(Duration::from_secs(2), notifications.recv())
        .await
        .expect("disconnect notification timed out")
        .expect("notification channel closed");

    assert_eq!(notification.method, notification::SERVER_DISCONNECTED);
}

#[tokio::test]
async fn request_after_disconnect_fails_instead_of_hanging() {
    let (client, _child, mut notifications, _crash_rx) = Client::spawn(exiting_server())
        .await
        .expect("spawn backend");
    client
        .request("turn/start", json!({}))
        .await
        .expect("turn/start response");
    while let Some(n) = notifications.recv().await {
        if n.method == notification::SERVER_DISCONNECTED {
            break;
        }
    }

    let late = tokio::time::timeout(
        Duration::from_secs(2),
        client.request("setup/status", json!({})),
    )
    .await
    .expect("request after disconnect hung");

    assert!(format!("{:#}", late.unwrap_err()).contains("app-server closed"));
}
