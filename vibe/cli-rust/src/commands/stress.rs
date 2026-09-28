//! `/stress` repeats history entries through the normal reducer.

use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};

use crate::app::App;
use crate::server::notification;
use crate::server::{Client, Notification};

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
    let mut entries: Vec<Value> = serde_json::from_str(HISTORY).expect("valid stress history JSON");
    assert!(!entries.is_empty(), "stress history must not be empty");
    // Local-but-not-historical entries never fold into a tool group, so bodies stay expanded.
    for entry in &mut entries {
        entry["local"] = Value::Bool(true);
    }
    entries
}

fn stress_entry(entries: &[Value], counter: u64) -> Value {
    let mut entry = entries[counter as usize % entries.len()].clone();
    let id = entry
        .get("id")
        .and_then(Value::as_str)
        .expect("stress history entry id");
    entry["id"] = Value::String(format!("stress-{counter:06}-{id}"));
    entry
}
