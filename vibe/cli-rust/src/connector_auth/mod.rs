//! Connector auth bottom-app state (Python `ConnectorAuthApp`).

mod rows;

use std::sync::Arc;

use serde_json::{json, Value};

use crate::app::{App, ToastSeverity};
use crate::clipboard;
use crate::external_url;
use crate::input::deliver;
use crate::server::{method, Client};

pub use rows::{detail, help_text, rows};

/// Seconds the copy toast stays up (Python `copy_text_to_clipboard` timeout).
const COPY_TOAST_SECS: u64 = 2;
/// Native-copy hint appended when the clipboard write is unverified.
const NATIVE_COPY_HINT: &str = "if paste fails, hold Shift (Option in iTerm2, Fn in Terminal.app) \
     while selecting for native copy";

/// One worker's answer, applied on the main thread.
pub enum Event {
    /// `connectors/auth/read` returned the connector's auth URL, if it has one.
    AuthUrl {
        generation: u64,
        url: Option<String>,
    },
    /// `connectors/refresh` re-discovered the connector's tools.
    Refreshed {
        generation: u64,
        tool_count: u64,
        runtime: Option<Value>,
    },
}

/// The three selectable options, in Python's order.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum OptionId {
    Open,
    Copy,
    Show,
}

/// One rendered row of the option list.
pub enum Row {
    Note(String),
    Blank,
    /// An action row; `key` is the `shortcut()` run inside its label.
    Action {
        id: OptionId,
        before: String,
        key: String,
        after: String,
    },
}

impl Row {
    pub fn selectable(&self) -> bool {
        matches!(self, Self::Action { .. })
    }
}

/// Open the app for `connector_name` and fetch its auth URL.
pub fn open(app: &mut App, client: &Arc<Client>, connector_name: String) {
    app.mcp.open = false;
    app.mcp.viewing_name = None;
    app.mcp.viewing_kind = None;
    app.connector_auth = crate::app::ConnectorAuthApp {
        open: true,
        connector_name,
        generation: app.connector_auth.generation + 1,
        tx: app.connector_auth.tx.clone(),
        ..Default::default()
    };
    fetch_auth_url(app, client);
}

/// Esc/Backspace, and the refresh that discovered tools (Python `ConnectorAuthClosed`).
pub fn close(app: &mut App, client: &Arc<Client>, refreshed: bool) {
    let connector_name = if refreshed {
        app.connector_auth.connector_name.clone()
    } else {
        String::new()
    };
    app.connector_auth.open = false;
    app.connector_auth.generation += 1;
    // Python only refreshes the banner here, then re-opens the browser; the
    // connector was already re-discovered by `connectors/refresh`.
    crate::mcp::reopen(app, client, false, connector_name);
}

fn fetch_auth_url(app: &mut App, client: &Arc<Client>) {
    let generation = app.connector_auth.generation;
    let name = app.connector_auth.connector_name.clone();
    let (Some(session_id), Some(tx)) = (
        app.session.session_id.clone(),
        app.connector_auth.tx.clone(),
    ) else {
        return;
    };
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let url = client
            .request(
                method::CONNECTOR_AUTH_READ,
                json!({"sessionId": session_id, "name": name}),
            )
            .await
            .ok()
            .and_then(|result| result.get("url").and_then(Value::as_str).map(str::to_owned));
        deliver(Some(tx), Event::AuthUrl { generation, url }, &pending).await;
    });
}

/// `R`: re-discover the connector's tools (Python `action_refresh`).
pub fn refresh(app: &mut App, client: &Arc<Client>) {
    app.connector_auth.status_message = Some("Refreshing connector...".to_owned());
    let generation = app.connector_auth.generation;
    let name = app.connector_auth.connector_name.clone();
    let (Some(session_id), Some(tx)) = (
        app.session.session_id.clone(),
        app.connector_auth.tx.clone(),
    ) else {
        return;
    };
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let result = client
            .request(
                method::CONNECTOR_REFRESH,
                json!({"sessionId": session_id, "name": name}),
            )
            .await
            .ok();
        let tool_count = result
            .as_ref()
            .and_then(|result| result.get("toolCount"))
            .and_then(Value::as_u64)
            .unwrap_or(0);
        let runtime = result.and_then(|result| result.get("runtime").cloned());
        let event = Event::Refreshed {
            generation,
            tool_count,
            runtime,
        };
        deliver(Some(tx), event, &pending).await;
    });
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    let generation = match &event {
        Event::AuthUrl { generation, .. } | Event::Refreshed { generation, .. } => *generation,
    };
    if app.connector_auth.open && generation == app.connector_auth.generation {
        match event {
            Event::AuthUrl { url, .. } => {
                app.connector_auth.auth_url = url;
                app.connector_auth.fetched = true;
                app.connector_auth.selected = 0;
            }
            Event::Refreshed {
                tool_count,
                runtime,
                ..
            } => {
                if let Some(runtime) = runtime {
                    crate::event_handler::apply_runtime_value(app, &runtime);
                }
                on_connector_refreshed(app, client, tool_count);
            }
        }
    }
    app.commit_finished();
}

fn on_connector_refreshed(app: &mut App, client: &Arc<Client>, tool_count: u64) {
    if tool_count > 0 {
        app.connector_auth.status_message = Some(format!("{tool_count} tools discovered."));
        close(app, client, true);
        return;
    }
    app.connector_auth.status_message = Some(
        "No tools discovered. Authentication may still be pending, try again in a moment."
            .to_owned(),
    );
}

/// Move the highlight, clamped like Textual's `OptionList`.
pub fn navigate(app: &mut App, down: bool) {
    let last = rows(app).iter().filter(|row| row.selectable()).count();
    if last == 0 {
        return;
    }
    app.connector_auth.selected = if down {
        (app.connector_auth.selected + 1).min(last - 1)
    } else {
        app.connector_auth.selected.saturating_sub(1)
    };
}

/// Enter on the highlighted option (Python `on_option_list_option_selected`).
pub fn select(app: &mut App) {
    match selected_id(app) {
        Some(OptionId::Open) => open_browser(app),
        Some(OptionId::Copy) => copy_url(app),
        Some(OptionId::Show) => toggle_url(app),
        None => {}
    }
}

/// Mouse press: highlight the option under the cursor (Textual `OptionList`).
pub fn press(app: &mut App, at: (u16, u16)) {
    if let Some(index) = action_at(app, at) {
        app.connector_auth.selected = index;
    }
}

/// Mouse release: a click on the highlighted option activates it, like Enter.
pub fn release(app: &mut App, at: (u16, u16)) {
    if action_at(app, at) == Some(app.connector_auth.selected) {
        select(app);
    }
}

/// The action option under a screen cell, from the last render's geometry.
fn action_at(app: &App, at: (u16, u16)) -> Option<usize> {
    let area = app.connector_auth.list_area;
    let inside = at.0 >= area.x && at.0 < area.right() && at.1 >= area.y && at.1 < area.bottom();
    if !inside {
        return None;
    }
    let line = (at.1 - area.y) as usize;
    let rows = rows(app);
    if !rows.get(line).is_some_and(Row::selectable) {
        return None;
    }
    rows.iter()
        .take(line + 1)
        .filter(|row| row.selectable())
        .count()
        .checked_sub(1)
}

fn selected_id(app: &App) -> Option<OptionId> {
    rows(app)
        .into_iter()
        .filter_map(|row| match row {
            Row::Action { id, .. } => Some(id),
            _ => None,
        })
        .nth(app.connector_auth.selected)
}

fn open_browser(app: &mut App) {
    let Some(url) = app.connector_auth.auth_url.clone() else {
        return;
    };
    external_url::open(&url);
    app.connector_auth.status_message = Some("Opened in browser.".to_owned());
}

fn copy_url(app: &mut App) {
    let Some(url) = app.connector_auth.auth_url.clone() else {
        return;
    };
    let message = if clipboard::copy_to_clipboard(&url) {
        "Auth URL copied to clipboard".to_owned()
    } else {
        format!("Auth URL copied to clipboard · {NATIVE_COPY_HINT}")
    };
    app.show_toast(message, ToastSeverity::Information, COPY_TOAST_SECS);
}

fn toggle_url(app: &mut App) {
    if app.connector_auth.auth_url.is_none() {
        return;
    }
    app.connector_auth.auth_url_visible = !app.connector_auth.auth_url_visible;
}
