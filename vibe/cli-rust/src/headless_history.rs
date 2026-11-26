//! Bounded paginated history reads for headless output.

use std::sync::Arc;

use anyhow::{bail, Context, Result};
use serde_json::{json, Value};

use crate::server::{method, Client};

const HISTORY_PAGE_LIMIT: usize = 500;
const MAX_HISTORY_ENTRIES: usize = 10_000;

/// Read the full history: page backward from the latest window, then reverse.
pub async fn read_history(client: &Arc<Client>, session_id: &str) -> Result<Value> {
    let mut pages: Vec<Vec<Value>> = Vec::new();
    let mut total = 0;
    let mut cursor: Option<String> = None;
    loop {
        let resp = client
            .request(
                method::SESSION_HISTORY_LIST,
                json!({
                    "sessionId": session_id,
                    "page": {
                        "cursor": cursor.as_deref(),
                        "limit": HISTORY_PAGE_LIMIT,
                        "direction": "backward",
                    },
                }),
            )
            .await?;
        let page = resp
            .get("items")
            .and_then(Value::as_array)
            .context("session/history/list response missing items")?
            .clone();
        total += page.len();
        if total > MAX_HISTORY_ENTRIES {
            bail!("session history exceeds {MAX_HISTORY_ENTRIES} entries");
        }
        let next = resp
            .get("nextCursor")
            .and_then(Value::as_str)
            .map(str::to_owned);
        if page.is_empty() && next.is_some() {
            bail!("session/history/list returned an empty page with a cursor");
        }
        pages.push(page);
        if next.is_none() {
            break;
        }
        if next == cursor {
            bail!("session/history/list returned a repeated cursor");
        }
        cursor = next;
    }
    // Pages arrive newest-first; each page is internally ascending.
    let entries = pages.into_iter().rev().flatten().collect();
    Ok(Value::Array(entries))
}
