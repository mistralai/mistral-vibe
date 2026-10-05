//! Older session history paged in automatically at the transcript top.

use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::mpsc::Sender;

use crate::app::App;
use crate::server::{method, Client, HISTORY_LIMIT};
use crate::transcript::HistoryCursor;

/// One `session/history/list` answer for the cursor that asked for it.
pub struct Event {
    cursor: HistoryCursor,
    page: Option<OlderPage>,
}

/// A paged answer; an empty one ends paging.
#[derive(Default)]
pub struct OlderPage {
    pub items: Vec<Value>,
    pub next_cursor: Option<String>,
}

/// Parse a reply; `None` marks it failed, missing `items` included.
pub fn parse_page(page: &Value) -> Option<OlderPage> {
    let items = page.get("items").and_then(Value::as_array)?.clone();
    let next_cursor = page
        .get("nextCursor")
        .and_then(Value::as_str)
        .map(str::to_owned);
    Some(OlderPage { items, next_cursor })
}

/// At most one page request is in flight.
#[derive(Default)]
pub struct OlderHistory {
    pub tx: Option<Sender<Event>>,
    loading: bool,
}

/// Python `_load_older_history_page` for the shown history, live or a `/resume` preview.
pub fn load_older_history_page(app: &mut App, client: &Arc<Client>) {
    // A saturated `u16` document height can no longer place older rows.
    if app.older_history.loading
        || !app.view.at_top
        || app.view.last_total == u16::MAX
        || app.subagents.viewed_subagent_id.is_some()
        || app.view.transcript_cache.preparing_history()
    {
        return;
    }
    let Some(cursor) = app.view.transcript.history_before_cursor().cloned() else {
        return;
    };
    let Some(tx) = app.older_history.tx.clone() else {
        return;
    };
    app.older_history.loading = true;
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let params = json!({
            "sessionId": cursor.session_id,
            "page": {"cursor": cursor.before, "limit": HISTORY_LIMIT, "direction": "backward"},
        });
        let page = client
            .request(method::SESSION_HISTORY_LIST, params)
            .await
            .inspect_err(|err| tracing::warn!(%err, "older history page failed"))
            .ok()
            .and_then(|page| parse_page(&page));
        crate::input::deliver(Some(tx), Event { cursor, page }, &pending).await;
    });
}

/// Prepend the answered page; a failure ends paging instead of retrying every frame.
pub fn apply_event(app: &mut App, event: Event) {
    app.older_history.loading = false;
    let failed = event.page.is_none();
    let page = event.page.unwrap_or_default();
    let Some(added) =
        app.view
            .transcript
            .prepend_history(&event.cursor, page.items, page.next_cursor)
    else {
        return;
    };
    if failed {
        crate::ui::notice::show_warning(app, "Failed to load older messages.", 3);
    }
    if added == 0 {
        return;
    }
    app.view.transcript_cache.start_older_history(added);
    // Selections and a pending toggle anchor point at rows and indices the prepended page shifts.
    app.view.scroll_anchor = None;
    crate::selection::cancel_drag(app);
    app.selection.region = None;
    app.expand_rebuilt_tools();
}
