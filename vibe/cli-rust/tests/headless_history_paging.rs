//! History paging against a scripted app-server.

use std::io::{BufRead, Write};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};
use vibe_rs::headless_history::read_history;
use vibe_rs::server::{Client, Launch};

const PAGE: usize = 500;
const TOTAL: usize = 1200;

#[test]
#[ignore]
fn fake_server_pages_history_backward() {
    for line in std::io::stdin().lock().lines() {
        let msg: Value = serde_json::from_str(&line.unwrap()).unwrap();
        if msg.get("id").is_none()
            || msg.get("method").and_then(Value::as_str) != Some("session/history/list")
        {
            continue;
        }
        let direction = msg
            .pointer("/params/page/direction")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let end = msg
            .pointer("/params/page/cursor")
            .and_then(Value::as_str)
            .map(|c| c.parse::<usize>().unwrap())
            .unwrap_or(TOTAL);
        let start = end.saturating_sub(PAGE);
        let items: Vec<Value> = (start..end).map(|i| json!({"id": i.to_string()})).collect();
        // Mirror the server: the latest window has no after-cursor, so a forward walk stops after one page.
        let older_exist = start > 0 && direction == "backward";
        let next = older_exist.then(|| json!(start.to_string()));
        let reply = json!({
            "jsonrpc": "2.0",
            "id": msg["id"],
            "result": {"items": items, "nextCursor": next},
        });
        println!("{}", serde_json::to_string(&reply).unwrap());
        std::io::stdout().flush().unwrap();
    }
}

#[tokio::test]
async fn read_history_walks_all_pages_in_ascending_order() {
    let launch = Launch {
        program: std::env::current_exe()
            .expect("test executable")
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            "fake_server_pages_history_backward".into(),
            "--nocapture".into(),
        ],
        cwd: None,
    };
    let (client, _child, _notifications, _crash_rx) =
        Client::spawn(launch).await.expect("spawn backend");
    let client = Arc::new(client);

    let history = tokio::time::timeout(Duration::from_secs(10), read_history(&client, "session-1"))
        .await
        .expect("read_history timed out")
        .expect("read_history failed");

    let entries = history.as_array().expect("history array");
    assert_eq!(entries.len(), TOTAL);
    let ids: Vec<usize> = entries
        .iter()
        .map(|e| e["id"].as_str().unwrap().parse().unwrap())
        .collect();
    assert_eq!(ids.first(), Some(&0));
    assert_eq!(ids.last(), Some(&(TOTAL - 1)));
    assert!(ids.windows(2).all(|w| w[0] + 1 == w[1]));
}
