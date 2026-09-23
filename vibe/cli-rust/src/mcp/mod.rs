//! `/mcp` browser state (Python `MCPApp` and `VibeApp._show_mcp`).

pub mod add_args;
mod browser;
pub mod commands;
mod help;
mod notices;
pub mod rows;
pub mod search;
pub mod search_usage;

use std::sync::Arc;
use std::time::Duration;

use crate::server::Client;
use crate::server::{method, MCPState};
use serde_json::{json, Value};

use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::input::deliver;
use crate::transcript::local;

pub use browser::{back, close, navigate, press, release, select, set_disabled, wheel};
pub use help::help_text;
pub use notices::show_post_init_notices;

/// How often the open browser re-discovers sources (Python `_BACKGROUND_REFRESH_INTERVAL_SECONDS`).
pub const BACKGROUND_REFRESH_INTERVAL: Duration = Duration::from_secs(60);

/// An answer to one `/mcp` server round-trip, applied on the main thread.
pub enum Event {
    /// `mcp/read` answered a `/mcp [name]` invocation: mount messages, then open.
    Opened {
        /// Runtime of the `mcp/refresh` an auth flow ran first, if any.
        runtime: Option<Value>,
        state: MCPState,
        initial_source: String,
    },
    /// A refresh or a toggle returned a new runtime for the open browser.
    Refreshed(Value),
    Error(String),
    SearchRecorded,
}

/// Run `/mcp` (Python `_show_mcp`): subcommands first, else read and open.
pub fn show(app: &mut App, client: &Arc<Client>, value: &str) {
    let args = value
        .split_once(char::is_whitespace)
        .map_or("", |(_, args)| args.trim());
    if commands::run(app, client, args) {
        return;
    }
    read_and_open(app, client, args.to_owned(), false);
}

/// Re-open the browser once an auth bottom-app closed (Python's `MCPOAuthClosed`
/// handler: `_refresh_mcp_browser` when it authenticated, then `_show_mcp`).
pub fn reopen(app: &mut App, client: &Arc<Client>, refreshed: bool, initial_source: String) {
    read_and_open(app, client, initial_source, refreshed);
}

/// Read the catalog and open the browser, re-discovering sources first when asked.
fn read_and_open(app: &mut App, client: &Arc<Client>, initial_source: String, refresh: bool) {
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.mcp.tx.clone()) else {
        return;
    };
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let mut runtime = None;
        if refresh {
            let params = json!({"sessionId": session_id});
            runtime = client.request(method::MCP_REFRESH, params).await.ok();
        }
        let result = client
            .request(method::MCP_READ, json!({"sessionId": session_id}))
            .await;
        let event = match result {
            Ok(value) => Event::Opened {
                runtime,
                state: state_at(&value, "/mcp"),
                initial_source,
            },
            Err(error) => Event::Error(format!("Failed to read MCP servers: {error}")),
        };
        deliver(Some(tx), event, &pending).await;
    });
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    match event {
        Event::Opened {
            runtime,
            state,
            initial_source,
        } => {
            if let Some(runtime) = runtime {
                crate::event_handler::apply_runtime_value(app, &runtime);
            }
            open(app, client, state, &initial_source);
        }
        // Python re-applies the whole runtime, refreshes the banner counts, and
        // rebuilds the rows while keeping the highlighted option.
        Event::Refreshed(runtime) => {
            let highlighted = current_row_id(app);
            crate::event_handler::apply_runtime_value(app, &runtime);
            app.mcp.state = state_at(&runtime, "/runtime/mcp");
            if let Some(id) = highlighted {
                select_row_id(app, &id);
            }
            reconcile_selection(app);
        }
        Event::Error(message) => add_error(app, &message),
        Event::SearchRecorded => {}
    }
    app.commit_finished();
}

/// Mount the read's messages and switch to the browser when it has sources.
fn open(app: &mut App, client: &Arc<Client>, state: MCPState, initial_source: &str) {
    if let Some(error) = &state.connector_error {
        add_error(
            app,
            &format!("Could not load workspace connectors.\n{error}"),
        );
    }
    if state.sources.is_empty() {
        if state.connector_error.is_none() {
            add_result(app, "No MCP servers or connectors configured.");
        }
        return;
    }
    if app.mcp.open {
        return;
    }
    if !initial_source.is_empty()
        && !state
            .sources
            .iter()
            .any(|source| source.name == initial_source)
    {
        let known: Vec<&str> = state
            .sources
            .iter()
            .map(|source| source.name.as_str())
            .collect();
        add_error(
            app,
            &format!(
                "Unknown MCP server or connector: {initial_source}. Known: {}",
                known.join(", ")
            ),
        );
        return;
    }
    add_result(app, "MCP and connectors opened...");
    app.mcp.state = state;
    app.mcp.search = search::Search::default();
    app.mcp.viewing_name = (!initial_source.is_empty()).then(|| initial_source.to_owned());
    app.mcp.viewing_kind = None;
    app.mcp.selected = 0;
    app.mcp.scroll = 0;
    app.mcp.free_scroll = false;
    app.mcp.open = true;
    reconcile_selection(app);
    refresh(app, client);
}

/// Re-discover sources for the open browser (Python `_refresh_mcp_browser`).
pub fn refresh(app: &mut App, client: &Arc<Client>) {
    request_state(app, client, method::MCP_REFRESH, json!({}));
}

/// Issue a request whose response carries a runtime, then apply its MCP state.
pub(super) fn request_state(
    app: &mut App,
    client: &Arc<Client>,
    method: &'static str,
    params: Value,
) {
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.mcp.tx.clone()) else {
        return;
    };
    let mut params = params;
    params["sessionId"] = json!(session_id);
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let event = match client.request(method, params).await {
            Ok(value) => Event::Refreshed(value),
            Err(error) => Event::Error(format!("Failed to reach MCP servers: {error}")),
        };
        deliver(Some(tx), event, &pending).await;
    });
}

pub(super) fn state_at(value: &Value, pointer: &str) -> MCPState {
    value
        .pointer(pointer)
        .cloned()
        .and_then(|state| serde_json::from_value(state).ok())
        .unwrap_or_default()
}

/// Keep the highlight on a selectable row after the rows were rebuilt.
pub(super) fn reconcile_selection(app: &mut App) {
    let rows = rows::rows(&app.mcp);
    if rows
        .get(app.mcp.selected)
        .is_some_and(|row| row.selectable())
    {
        return;
    }
    app.mcp.selected = rows
        .iter()
        .position(|row| row.selectable())
        .unwrap_or(app.mcp.selected.min(rows.len().saturating_sub(1)));
}

fn current_row_id(app: &App) -> Option<String> {
    rows::rows(&app.mcp).get(app.mcp.selected)?.id()
}

fn select_row_id(app: &mut App, id: &str) {
    if let Some(index) = rows::rows(&app.mcp)
        .iter()
        .position(|row| row.id().as_deref() == Some(id))
    {
        app.mcp.selected = index;
    }
}

pub(super) fn add_result(app: &mut App, text: &str) {
    local::add_command_result(&mut app.view.transcript, &new_message_id(), text);
}

fn add_error(app: &mut App, text: &str) {
    local::add_command_error(&mut app.view.transcript, &new_message_id(), text);
}
