//! `d`/`e` toggles of the `/mcp` browser (Python `_set_highlighted_disabled`).

use std::sync::Arc;

use crate::server::{method, Client, MCPSourceKind, MCPSourceStatus, MCPSourceSummary};
use serde_json::json;

use super::rows::{self, Row};
use super::{Event, TOAST_SECS};
use crate::app::{App, ToastSeverity};
use crate::input::deliver;

/// One toggle request (Python `MCPToggled`), kept to undo its paint on rejection.
pub struct Toggle {
    pub name: String,
    pub kind: MCPSourceKind,
    pub tool_name: Option<String>,
}

/// `d`/`e` on the highlighted source or tool.
/// Only a disable is painted up front: whether an enabled source connects is
/// up to discovery and auth, so its status waits for the server's answer.
pub fn set_disabled(app: &mut App, client: &Arc<Client>, disabled: bool) {
    let (name, kind, tool_name) = match rows::rows(&app.mcp).into_iter().nth(app.mcp.selected) {
        Some(Row::Source(row)) => (row.name, Some(row.kind), None),
        Some(Row::Tool(row)) => match app.mcp.viewing_name.clone() {
            Some(name) => (name, app.mcp.viewing_kind, Some(row.name)),
            None => return,
        },
        _ => return,
    };
    // The viewed source's own kind, which `/mcp <name>` leaves unset.
    let Some(kind) = rows::find_source(&app.mcp.state, &name, kind).map(|source| source.kind)
    else {
        return;
    };
    if reject_plugin_toggle(app, &name, kind) {
        return;
    }
    let toggle = Toggle {
        name,
        kind,
        tool_name,
    };
    let Some(source) = source_mut(&mut app.mcp.state.sources, &toggle) else {
        return;
    };
    match &toggle.tool_name {
        Some(tool_name) => {
            let Some(tool) = source.tools.iter_mut().find(|tool| &tool.name == tool_name) else {
                return;
            };
            tool.enabled = !disabled;
        }
        None if disabled => source.status = MCPSourceStatus::Disabled,
        None => {}
    }
    request_toggle(app, client, toggle, disabled);
}

/// A plugin server has no `[[mcp_servers]]` entry to write the toggle to.
fn reject_plugin_toggle(app: &mut App, name: &str, kind: MCPSourceKind) -> bool {
    let Some(plugin) = rows::find_source(&app.mcp.state, name, Some(kind))
        .and_then(|source| source.plugin_name.clone())
    else {
        return false;
    };
    app.show_toast(
        format!("{name} is managed by the {plugin} plugin and cannot be toggled here."),
        ToastSeverity::Warning,
        TOAST_SECS,
    );
    true
}

/// Send a toggle; its answer carries the runtime the browser then adopts.
fn request_toggle(app: &mut App, client: &Arc<Client>, toggle: Toggle, disabled: bool) {
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.mcp.tx.clone()) else {
        return;
    };
    let (method, mut params) = match toggle.kind {
        MCPSourceKind::Connector => (
            method::CONNECTOR_TOGGLE,
            json!({"alias": toggle.name, "disabled": disabled, "toolName": toggle.tool_name}),
        ),
        MCPSourceKind::Server => (
            method::MCP_TOGGLE,
            json!({
                "name": toggle.name,
                "source": toggle.kind.as_str(),
                "disabled": disabled,
                "toolName": toggle.tool_name,
            }),
        ),
    };
    params["sessionId"] = json!(session_id);
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let event = match client.request(method, params).await {
            Ok(value) => Event::Toggled(value),
            Err(error) => Event::ToggleFailed {
                toggle,
                message: error.to_string(),
            },
        };
        deliver(Some(tx), event, &pending).await;
    });
}

/// Undo only this rejected toggle's paint, keeping the paint of toggles still in flight.
pub(super) fn revert(app: &mut App, toggle: &Toggle) {
    let mcp = &mut app.mcp;
    let Some(confirmed) = rows::find_source(&mcp.confirmed, &toggle.name, Some(toggle.kind)) else {
        return;
    };
    let Some(source) = source_mut(&mut mcp.state.sources, toggle) else {
        return;
    };
    let Some(tool_name) = &toggle.tool_name else {
        source.status = confirmed.status;
        return;
    };
    let confirmed_tool = confirmed.tools.iter().find(|tool| &tool.name == tool_name);
    let tool = source.tools.iter_mut().find(|tool| &tool.name == tool_name);
    if let (Some(confirmed_tool), Some(tool)) = (confirmed_tool, tool) {
        tool.enabled = confirmed_tool.enabled;
    }
}

fn source_mut<'a>(
    sources: &'a mut [MCPSourceSummary],
    toggle: &Toggle,
) -> Option<&'a mut MCPSourceSummary> {
    sources
        .iter_mut()
        .find(|source| source.name == toggle.name && source.kind == toggle.kind)
}
