//! MCP OAuth bottom-app state (Python `MCPOAuthApp`).

use std::sync::Arc;

use serde_json::json;

use crate::app::{App, ToastSeverity};
use crate::clipboard;
use crate::external_url;

use crate::server::{method, Client};

/// Shortcut line of the app (Python `_HELP`).
pub const HELP: &str = "R Retry  Backspace Back";
/// Indent of the three action options (Python `_OPTION_PADDING`).
const OPTION_PADDING: &str = "  ";
/// Seconds the copy toast stays up (Python `copy_text_to_clipboard` timeout).
const COPY_TOAST_SECS: u64 = 2;
/// Native-copy hint appended when the clipboard write is unverified.
const NATIVE_COPY_HINT: &str = "if paste fails, hold Shift (Option in iTerm2, Fn in Terminal.app) \
     while selecting for native copy";

/// The answer of one `mcp/login` round-trip, applied on the main thread.
pub struct Event {
    /// Login generation, so a retry cannot be settled by a stale attempt.
    pub generation: u64,
    pub error: Option<String>,
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
    Action(OptionId, String),
}

impl Row {
    pub fn selectable(&self) -> bool {
        matches!(self, Self::Action(_, _))
    }
}

/// Open the app for `server_name` and start its login (Python `MCPOAuthRequested`).
pub fn open(app: &mut App, client: &Arc<Client>, server_name: String) {
    app.mcp.open = false;
    app.mcp.viewing_name = None;
    app.mcp.viewing_kind = None;
    app.mcp_oauth.open = true;
    app.mcp_oauth.server_name = server_name;
    start_login(app, client);
}

/// Esc/Backspace, and the successful login (Python `MCPOAuthClosed`).
pub fn close(app: &mut App, client: &Arc<Client>, refreshed: bool) {
    let server_name = if refreshed {
        app.mcp_oauth.server_name.clone()
    } else {
        String::new()
    };
    app.mcp_oauth.open = false;
    app.mcp_oauth.generation += 1;
    crate::mcp::reopen(app, client, refreshed, server_name);
}

/// `R`: start the login again unless one is already running.
pub fn refresh(app: &mut App, client: &Arc<Client>) {
    if app.mcp_oauth.logging_in {
        return;
    }
    start_login(app, client);
}

fn start_login(app: &mut App, client: &Arc<Client>) {
    app.mcp_oauth.auth_url = None;
    app.mcp_oauth.auth_url_visible = false;
    app.mcp_oauth.failed = false;
    app.mcp_oauth.selected = 0;
    app.mcp_oauth.logging_in = true;
    app.mcp_oauth.status_message = Some("Preparing authentication...".to_owned());
    app.mcp_oauth.generation += 1;
    let generation = app.mcp_oauth.generation;
    let name = app.mcp_oauth.server_name.clone();
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.mcp_oauth.tx.clone())
    else {
        return;
    };
    let client = client.clone();
    // The login answer repaints the bottom app, so hold the replay marker with a
    // commit until it lands (mirrors `connector_auth`), else the capture races
    // the "Preparing authentication..." placeholder.
    let pending = app.commit_started();
    tokio::spawn(async move {
        let error = client
            .request(
                method::MCP_LOGIN,
                json!({"sessionId": session_id, "name": name}),
            )
            .await
            .err()
            .map(|error| error.to_string());
        crate::input::deliver(Some(tx), Event { generation, error }, &pending).await;
    });
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    if app.mcp_oauth.open && event.generation == app.mcp_oauth.generation {
        app.mcp_oauth.logging_in = false;
        match event.error {
            None => close(app, client, true),
            Some(error) => on_login_failed(app, error),
        }
    }
    app.commit_finished();
}

/// The `mcp/authUrl` notification of the running login.
pub fn on_auth_url(app: &mut App, url: &str) {
    app.mcp_oauth.auth_url = Some(url.to_owned());
    app.mcp_oauth.selected = 0;
    app.mcp_oauth.status_message = Some("Waiting for browser sign-in...".to_owned());
}

fn on_login_failed(app: &mut App, message: String) {
    app.mcp_oauth.failed = true;
    app.mcp_oauth.status_message = Some(message);
}

/// Move the highlight, clamped like Textual's `OptionList`.
pub fn navigate(app: &mut App, down: bool) {
    let last = rows(app).iter().filter(|row| row.selectable()).count();
    if last == 0 {
        return;
    }
    app.mcp_oauth.selected = if down {
        (app.mcp_oauth.selected + 1).min(last - 1)
    } else {
        app.mcp_oauth.selected.saturating_sub(1)
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
        app.mcp_oauth.selected = index;
    }
}

/// Mouse release: a click on the highlighted option activates it, like Enter.
pub fn release(app: &mut App, at: (u16, u16)) {
    if action_at(app, at) == Some(app.mcp_oauth.selected) {
        select(app);
    }
}

/// The action option under a screen cell, from the last render's geometry.
fn action_at(app: &App, at: (u16, u16)) -> Option<usize> {
    let area = app.mcp_oauth.list_area;
    let inside = at.0 >= area.x && at.0 < area.right() && at.1 >= area.y && at.1 < area.bottom();
    if !inside {
        return None;
    }
    let line = (at.1 - area.y) as usize;
    rows(app)
        .iter()
        .take(line + 1)
        .filter(|row| row.selectable())
        .count()
        .checked_sub(1)
        .filter(|_| rows(app).get(line).is_some_and(Row::selectable))
}

fn selected_id(app: &App) -> Option<OptionId> {
    rows(app)
        .into_iter()
        .filter_map(|row| match row {
            Row::Action(id, _) => Some(id),
            _ => None,
        })
        .nth(app.mcp_oauth.selected)
}

fn open_browser(app: &mut App) {
    let Some(url) = app.mcp_oauth.auth_url.clone() else {
        return;
    };
    external_url::open(&url);
    app.mcp_oauth.status_message = Some("Opened in browser.".to_owned());
}

fn copy_url(app: &mut App) {
    let Some(url) = app.mcp_oauth.auth_url.clone() else {
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
    if app.mcp_oauth.auth_url.is_none() {
        return;
    }
    app.mcp_oauth.auth_url_visible = !app.mcp_oauth.auth_url_visible;
}

/// The option rows of the current phase: starting, failed, or awaiting sign-in.
pub fn rows(app: &App) -> Vec<Row> {
    if app.mcp_oauth.failed {
        return vec![Row::Note(
            "Authentication failed. Press R to retry.".to_owned(),
        )];
    }
    let Some(_) = app.mcp_oauth.auth_url.as_deref() else {
        return vec![Row::Note("Starting OAuth login...".to_owned())];
    };
    vec![
        Row::Note("This MCP server requires authentication".to_owned()),
        Row::Blank,
        Row::Action(
            OptionId::Open,
            format!("{OPTION_PADDING}Press enter to open auth in your browser"),
        ),
        Row::Action(
            OptionId::Copy,
            format!("{OPTION_PADDING}Copy URL to clipboard"),
        ),
        Row::Action(
            OptionId::Show,
            format!("{OPTION_PADDING}Manually show the URL"),
        ),
    ]
}

/// The static under the options (Python `_update_detail_text`).
pub fn detail(app: &App) -> String {
    let url = app.mcp_oauth.auth_url.as_deref();
    if app.mcp_oauth.failed || url.is_none() {
        return String::new();
    }
    let mut parts: Vec<&str> = Vec::new();
    if let (true, Some(url)) = (app.mcp_oauth.auth_url_visible, url) {
        parts.push(url);
        parts.push("");
    }
    parts.push("Once authenticated in your browser, return to Vibe");
    parts.join("\n")
}

/// The help line, prefixed with the current status (Python `_set_help_text`).
pub fn help_text(app: &App) -> String {
    match &app.mcp_oauth.status_message {
        Some(status) => format!("{status}  {HELP}"),
        None => HELP.to_owned(),
    }
}
