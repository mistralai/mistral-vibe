//! `/mcp` browser state.

pub mod add_args;
mod browser;
pub mod commands;
mod connect;
mod help;
mod labels;
mod notices;
mod refresh;
pub mod rows;
pub mod scope;
pub mod search;
mod toggle;

use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::server::Client;
use crate::server::{method, MCPState};
use serde_json::{json, Value};

use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::transcript::local;
use rows::Row;

pub use browser::{back, close, mouse, navigate, select};
pub use help::help_text;
pub use notices::show_post_init_notices;
pub(crate) use notices::INTEGRATIONS_UNAVAILABLE_TOAST_SECS;
use refresh::rediscover;
pub use refresh::{background_refresh, refresh, refresh_deadline};
pub use toggle::{set_disabled, Toggle};

/// How often the open browser re-discovers sources.
pub const BACKGROUND_REFRESH_INTERVAL: Duration = Duration::from_secs(60);
/// Seconds a warning toast stays up.
const TOAST_SECS: u64 = 5;

/// An answer to one `/mcp` server round-trip, applied on the main thread.
pub enum Event {
    /// `mcp/read` answered a `/mcp [name]` invocation: mount messages, then open.
    Opened {
        /// Response of the `mcp/refresh` an auth flow ran first, if any.
        response: Option<Value>,
        state: MCPState,
        initial_source: String,
        /// Reopened by a finished auth flow, which must not start another one.
        reopened: bool,
    },
    /// A refresh finished with its response, or `None` when it failed.
    Refreshed(Option<Value>),
    /// A toggle was accepted; its response carries the new runtime.
    Toggled(Value),
    /// A toggle was rejected; its optimistic flip is rolled back.
    ToggleFailed {
        toggle: Toggle,
        message: String,
    },
    /// The viewed source has a pending action to start.
    ActionRequested,
    /// `connector_catalog/connect` for `name` answered; its response carries the new runtime.
    Connected {
        name: String,
        response: Value,
    },
    /// `connector_catalog/connect` for `name` failed with this message.
    ConnectFailed {
        name: String,
        message: String,
    },
    Error(String),
}

/// Run `/mcp`: subcommands first, else read and open.
pub fn show(app: &mut App, client: &Arc<Client>, value: &str) {
    let args = value
        .split_once(char::is_whitespace)
        .map_or("", |(_, args)| args.trim());
    if commands::run(app, client, args) {
        return;
    }
    read_and_open(app, client, args.to_owned(), false, false);
}

/// Re-open the browser once an auth bottom-app closed, refreshing it first when
/// it authenticated.
pub fn reopen(app: &mut App, client: &Arc<Client>, refreshed: bool, initial_source: String) {
    read_and_open(app, client, initial_source, refreshed, true);
}

/// Read the catalog and open the browser, re-discovering sources first when asked.
fn read_and_open(
    app: &mut App,
    client: &Arc<Client>,
    initial_source: String,
    refresh: bool,
    reopened: bool,
) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let params = json!({"sessionId": session_id});
        let mut response = None;
        if refresh {
            response = rediscover(&client, params.clone()).await.ok();
        }
        match client.request(method::MCP_READ, params.clone()).await {
            Ok(value) => Event::Opened {
                response,
                state: with_connector_catalog(
                    &client,
                    params,
                    state_at(&value, "/mcp").unwrap_or_default(),
                )
                .await,
                initial_source,
                reopened,
            },
            Err(error) => Event::Error(format!("Failed to read MCP servers: {error}")),
        }
    });
}

/// Fill the Studio link and private-connectors warning from `connector_catalog/read`.
async fn with_connector_catalog(client: &Client, params: Value, mut state: MCPState) -> MCPState {
    let Ok(catalog) = client.request(method::CONNECTOR_CATALOG_READ, params).await else {
        return state;
    };
    if state.manage_connectors_url.is_none() {
        state.manage_connectors_url = catalog
            .get("manageUrl")
            .and_then(Value::as_str)
            .map(str::to_owned);
    }
    state.private_connectors_hidden = catalog
        .get("privateConnectorsHidden")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    state
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    match event {
        Event::Opened {
            response,
            state,
            initial_source,
            reopened,
        } => {
            crate::mcp_oauth::dismiss(app);
            // A reopen that raced an open browser shows its list rather than loop back into auth.
            if reopened && viewing_pending_action(app, &state) {
                back(app);
            }
            if let Some(response) = response {
                crate::event_handler::apply_response_runtime(app, &response);
            }
            open(app, state, &initial_source, reopened);
        }
        Event::Refreshed(response) => {
            app.mcp.refreshing = false;
            if let Some(response) = response {
                crate::event_handler::apply_response_runtime(app, &response);
            }
        }
        Event::Toggled(response) => {
            crate::event_handler::apply_response_runtime(app, &response);
            // Without `runtime` the optimistic paint stays stale, so re-read.
            if !response.get("runtime").is_some_and(Value::is_object) {
                read_and_open(app, client, String::new(), false, false);
            }
        }
        // Notify and repaint from the server; only this toggle's paint is stale.
        Event::ToggleFailed { toggle, message } => {
            toggle::revert(app, &toggle);
            app.show_toast(message, crate::app::ToastSeverity::Warning, TOAST_SECS);
        }
        Event::ActionRequested => browser::hand_off(app, client),
        Event::Connected { name, response } => connect::finished(app, client, &name, &response),
        Event::ConnectFailed { name, message } => connect::failed(app, &name, &message),
        Event::Error(message) => {
            crate::mcp_oauth::dismiss(app);
            add_error(app, &message);
        }
    }
}

fn viewing_pending_action(app: &App, state: &MCPState) -> bool {
    app.mcp.viewing_name.as_deref().is_some_and(|name| {
        rows::find_source(state, name, app.mcp.viewing_kind)
            .is_some_and(|source| rows::pending_action(state, source).is_some())
    })
}

/// Mount the read's messages and switch to the browser when it has sources.
fn open(app: &mut App, mut state: MCPState, initial_source: &str, reopened: bool) {
    if let Some(error) = &state.connector_error {
        add_error(app, &format!("Could not load connectors.\n{error}"));
    }
    if app.mcp.open {
        // A re-read whose catalog read failed must not lift the block.
        state.private_connectors_hidden |= app.mcp.state.private_connectors_hidden;
        set_state(app, state);
        return;
    }
    let viewing = state.resolve_source(initial_source);
    if !initial_source.is_empty() && viewing.is_none() {
        let known: Vec<&str> = state.sources.iter().map(|source| source.label()).collect();
        add_error(
            app,
            &format!(
                "Unknown MCP server or connector: {initial_source}. Known: {}",
                known.join(", ")
            ),
        );
        return;
    }
    // After a flow that left it unusable, show the list rather than loop back.
    let viewing =
        viewing.filter(|source| !(reopened && rows::pending_action(&state, source).is_some()));
    app.mcp.search = crate::search_field::Search::default();
    app.mcp.viewing_name = viewing.map(|source| source.name.clone());
    app.mcp.viewing_kind = viewing.map(|source| source.kind);
    app.mcp.list.reset();
    app.mcp.open = true;
    app.mcp.refresh_at = Some(Instant::now() + BACKGROUND_REFRESH_INTERVAL);
    set_state(app, state);
}

/// Adopt the MCP state a runtime carries while the browser is open.
pub fn apply_runtime(app: &mut App, runtime: &Value) {
    if !app.mcp.open {
        return;
    }
    if let Some(mut state) = state_at(runtime, "/runtime/mcp") {
        // The runtime never carries the catalog fields, so keep the ones the read resolved.
        if state.manage_connectors_url.is_none() {
            state.manage_connectors_url = app.mcp.state.manage_connectors_url.clone();
        }
        state.private_connectors_hidden |= app.mcp.state.private_connectors_hidden;
        set_state(app, state);
    }
}

pub(super) fn state_at(value: &Value, pointer: &str) -> Option<MCPState> {
    serde::Deserialize::deserialize(value.pointer(pointer)?).ok()
}

/// Replace the server state and rebuild the view, keeping the highlight.
fn set_state(app: &mut App, state: MCPState) {
    let highlighted = current_row_id(app);
    app.mcp.confirmed = state.clone();
    app.mcp.state = state;
    if app.mcp.viewing_name.is_some() && rows::viewing_source(&app.mcp).is_none() {
        back(app);
        return;
    }
    if let Some(id) = highlighted {
        select_row_id(app, &id);
    }
    reconcile_selection(app);
    browser::request_action(app);
}

/// Keep the highlight on a selectable row after the rows were rebuilt, landing
/// on a source rather than the manage-connectors action.
pub(super) fn reconcile_selection(app: &mut App) {
    let rows = rows::rows(&app.mcp);
    if rows
        .get(app.mcp.list.selected())
        .is_some_and(|row| row.selectable())
    {
        return;
    }
    app.mcp.list.highlight(
        rows.iter()
            .position(|row| matches!(row, Row::Source(_)))
            .or_else(|| rows.iter().position(Row::selectable))
            .unwrap_or(app.mcp.list.selected().min(rows.len().saturating_sub(1))),
    );
}

fn current_row_id(app: &App) -> Option<String> {
    rows::rows(&app.mcp).get(app.mcp.list.selected())?.id()
}

fn select_row_id(app: &mut App, id: &str) {
    if let Some(index) = rows::rows(&app.mcp)
        .iter()
        .position(|row| row.id().as_deref() == Some(id))
    {
        app.mcp.list.highlight(index);
    }
}

fn add_error(app: &mut App, text: &str) {
    local::add_command_error(&mut app.view.transcript, &new_message_id(), text);
}
