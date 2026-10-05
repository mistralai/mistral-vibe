//! Child history fetch pipeline (Python `_refresh_subagent_transcript`).

use std::collections::HashSet;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::server::{method, Client};

use super::child::{Event, HISTORY_PAGE_LIMIT, HISTORY_RESUME_TAIL_MESSAGES};
use super::transcripts::entry_id;

/// Fetch one child's history (Python `_refresh_subagent_transcript`).
pub fn fetch_history(
    client: Arc<Client>,
    session_id: String,
    tx: Option<mpsc::Sender<Event>>,
    known_ids: HashSet<String>,
    cached_complete: bool,
    pending: Arc<AtomicU32>,
) {
    tokio::spawn(async move {
        let event = match fetch(&client, &session_id, &known_ids, cached_complete).await {
            Ok((history, complete)) => Event::Fetched {
                session_id,
                history,
                complete,
            },
            Err(_) => Event::Failed { session_id },
        };
        // The main thread releases the commit once it has applied the answer.
        let sent = match tx {
            Some(tx) => tx.send(event).await.is_ok(),
            None => false,
        };
        if !sent {
            pending.fetch_sub(1, Ordering::Relaxed);
        }
    });
}

async fn fetch(
    client: &Arc<Client>,
    session_id: &str,
    known_ids: &HashSet<String>,
    cached_complete: bool,
) -> anyhow::Result<(Vec<Value>, bool)> {
    let params = json!({
        "sessionId": session_id,
        "history": {"cursor": null, "limit": HISTORY_RESUME_TAIL_MESSAGES, "direction": "backward"},
        "turns": null,
    });
    let result = client.request(method::SESSION_READ, params).await?;
    let mut history: Vec<Value> = result
        .pointer("/state/history")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut complete = history.len() < HISTORY_RESUME_TAIL_MESSAGES as usize;
    if !complete && needs_complete(known_ids, cached_complete, &history) {
        let mut cursor = history.first().and_then(entry_id).map(str::to_owned);
        while let Some(before) = cursor {
            let params = json!({
                "sessionId": session_id,
                "page": {"cursor": before, "limit": HISTORY_PAGE_LIMIT, "direction": "backward"},
            });
            let page = client.request(method::SESSION_HISTORY_LIST, params).await?;
            let items = page
                .get("items")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            cursor = page
                .get("nextCursor")
                .and_then(Value::as_str)
                .filter(|cursor| !cursor.is_empty())
                .map(str::to_owned);
            let mut merged = items;
            merged.append(&mut history);
            history = merged;
        }
        complete = true;
    }
    Ok((history, complete))
}

/// Python `needs_complete_history` evaluated against the cached snapshot.
pub fn needs_complete(known_ids: &HashSet<String>, cached_complete: bool, tail: &[Value]) -> bool {
    if !cached_complete {
        return true;
    }
    if tail.is_empty() {
        return false;
    }
    !tail
        .iter()
        .filter_map(entry_id)
        .any(|id| known_ids.contains(id))
}
