//! `/stress` repeats history entries through the normal reducer.

use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};

use crate::app::App;
use crate::server::notification;
use crate::server::{Client, Notification};
use crate::transcript::grouping::KEY_PREFIX;

const HISTORY: &str = include_str!("stress_history.data");
const DEFAULT_COUNT: u64 = 100;
const MAX_COUNT: u64 = 100_000;
const RATE: f64 = 10_000.0;

/// Whether the firehose is still emitting; it keeps the pulse ticking so in-progress rows repaint.
pub fn running(app: &App) -> bool {
    app.stress
        .as_ref()
        .is_some_and(|handle| !handle.is_finished())
}

/// Stop the firehose if it is running. Returns whether it was running, so the
/// caller can treat the keypress as consumed (e.g. Ctrl+C).
pub fn stop(app: &mut App) -> bool {
    match app.stress.take() {
        // A finished run leaves its handle behind; treat it as not running so the
        // next `/stress` starts fresh instead of aborting a dead task (a no-op).
        Some(handle) if !handle.is_finished() => {
            handle.abort();
            true
        }
        _ => false,
    }
}

/// Toggle the firehose. First call emits `args` entries then stops (default
/// 100), a second call aborts it early (mirrors Python's `stress.toggle`).
pub fn toggle(app: &mut App, client: &Arc<Client>, args: &str) {
    if stop(app) {
        return;
    }
    let count = args
        .trim()
        .parse::<i64>()
        .unwrap_or(DEFAULT_COUNT as i64)
        .clamp(1, MAX_COUNT as i64) as u64;
    let delay = Duration::from_secs_f64(1.0 / RATE);
    let tx = client.notif_sender();
    let entries = entries();
    // Entry 1 opens the connector tool group; unfold it like the VIBE-4901 session.
    for counter in (1..count).step_by(entries.len()) {
        let key = format!("{KEY_PREFIX}{}", stress_id(counter, "buildkite-call-00"));
        app.view.expanded.insert(key);
    }
    app.stress = Some(tokio::spawn(async move {
        let mut ticker = tokio::time::interval(delay);
        for counter in 0..count {
            ticker.tick().await;
            let n = Notification {
                method: notification::HISTORY_ENTRY_ADDED.to_string(),
                params: json!({"entry": stress_entry(&entries, counter)}),
            };
            if tx.send(n).await.is_err() {
                break;
            }
        }
    }));
}

fn entries() -> Vec<Value> {
    let history: Vec<Value> = serde_json::from_str(HISTORY).expect("valid stress history JSON");
    assert!(!history.is_empty(), "stress history must not be empty");
    let mut entries = tool_group();
    entries.extend(history);
    // Local-but-not-historical entries never fold into a tool group, so bodies stay expanded.
    for entry in &mut entries {
        entry["local"] = Value::Bool(true);
    }
    entries
}

fn generated(count: usize, row: impl Fn(usize) -> String) -> String {
    (1..=count).map(|n| row(n) + "\n").collect()
}

/// A finished connector tool group shaped like the VIBE-4901 session: 40 calls with 2-160 KB results.
fn tool_group() -> Vec<Value> {
    let summary = generated(1_200, |n| match n % 250 {
        0 => format!("\"body_html\": \"{}\",", "annotation ".repeat(2_100)),
        _ => format!("\"step_{n}\": \"job step {n} finished with exit status 0\","),
    });
    let artifacts = generated(1_000, |n| {
        format!(
            "  \"path\": \"client-e2e/goldens/scenario_{}/frame_{n:04}.svg\",",
            n % 97
        )
    });
    let artifact = generated(10, |n| {
        let fill = if n == 1 { 1_700 } else { 200 };
        format!(
            "\"download_url_{n}\": \"https://s3.example.com/{}\",",
            "a".repeat(fill)
        )
    });
    let prompt = json!({
        "id": "buildkite-prompt",
        "sessionId": "demo-session",
        "turnId": "demo-turn",
        "createdAt": 0,
        "updatedAt": 0,
        "generationStatus": "completed",
        "relatedEntryId": null,
        "type": "message",
        "role": "user",
        "content": [{"type": "text", "text": "Why did the Buildkite build fail?"}],
        "source": "turn_start",
        "userDisplayContent": null
    });
    let calls = [
        ("get_build_failure_summary", &summary, 2),
        ("list_artifacts_for_job", &artifacts, 24),
        ("get_artifact", &artifact, 14),
    ]
    .into_iter()
    .flat_map(|(tool, text, count)| std::iter::repeat_n((tool, text), count))
    .enumerate()
    .map(|(index, (tool, text))| connector_call(index, tool, text));
    std::iter::once(prompt).chain(calls).collect()
}

fn connector_call(index: usize, tool: &str, text: &str) -> Value {
    let tool = format!("connector_buildkite.{tool}");
    let approval =
        json!({"approvalSource": "bypass", "approvalType": "always", "decision": "execute"});
    json!({
        "id": format!("buildkite-call-{index:02}"),
        "sessionId": "demo-session",
        "turnId": "demo-turn",
        "createdAt": 0,
        "updatedAt": 0,
        "generationStatus": "completed",
        "relatedEntryId": null,
        "type": "effect",
        "title": tool,
        "historical": true,
        "detail": {
            "toolName": tool,
            "input": {"build_number": "241185", "page": index},
            "display": {
                "summary": tool,
                "verb": "Running",
                "message": tool,
                "settledVerb": "Ran",
                "settledMessage": tool,
                "statusText": format!("Running {tool}")
            }
        },
        "state": {
            "approvalSource": "bypass",
            "approvalType": "always",
            "decision": "execute",
            "status": "completed",
            "display": {"message": tool, "success": true},
            "output": {
                "_meta": {"approval": approval},
                "content": [{"type": "text", "text": text}],
                "type": "success"
            },
            "outputText": text
        }
    })
}

fn stress_id(counter: u64, id: &str) -> String {
    format!("stress-{counter:06}-{id}")
}

fn stress_entry(entries: &[Value], counter: u64) -> Value {
    let mut entry = entries[counter as usize % entries.len()].clone();
    let id = entry
        .get("id")
        .and_then(Value::as_str)
        .expect("stress history entry id");
    entry["id"] = Value::String(stress_id(counter, id));
    entry
}
