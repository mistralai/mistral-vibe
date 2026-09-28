//! Keyboard actions of the open `/mcp` browser (Python `MCPApp` actions).

use std::sync::Arc;

use crate::server::Client;
use crate::server::{method, MCPSourceKind, MCPSourceStatus, MCPSourceSummary};
use serde_json::json;

use super::rows::{self, Row};
use super::{add_result, reconcile_selection, request_state};
use crate::app::App;

/// Esc: close the browser and mount the closed message (Python `MCPClosed`).
pub fn close(app: &mut App) {
    app.mcp.open = false;
    app.mcp.search = super::search::Search::default();
    app.mcp.viewing_name = None;
    app.mcp.viewing_kind = None;
    add_result(app, "MCP and connectors closed.");
}

/// Backspace: leave the tool list for the source list, highlighting its first
/// row as Python's `_show_list_view` does.
pub fn back(app: &mut App) {
    if app.mcp.viewing_name.is_none() {
        return;
    }
    app.mcp.viewing_name = None;
    app.mcp.viewing_kind = None;
    app.mcp.selected = 0;
    app.mcp.scroll = 0;
    app.mcp.free_scroll = false;
    reconcile_selection(app);
}

/// Enter: open the highlighted source's tools, or hand it to the auth flow.
pub fn select(app: &mut App, client: &Arc<Client>) {
    let Some(Row::Source(row)) = rows::rows(&app.mcp).into_iter().nth(app.mcp.selected) else {
        return;
    };
    // The auth flows own these sources; the browser hands the source over and
    // closes (Python posts `MCPOAuthRequested` / `ConnectorAuthRequested`).
    if row.needs_auth {
        match row.kind {
            MCPSourceKind::Server => crate::mcp_oauth::open(app, client, row.name),
            MCPSourceKind::Connector => crate::connector_auth::open(app, client, row.name),
        }
        return;
    }
    app.mcp.search.focused = false;
    app.mcp.viewing_name = Some(row.name);
    app.mcp.viewing_kind = Some(row.kind);
    app.mcp.selected = 0;
    app.mcp.scroll = 0;
    app.mcp.free_scroll = false;
    reconcile_selection(app);
}

/// Move the highlight to the next selectable row, clamped (Textual `OptionList`).
pub fn navigate(app: &mut App, down: bool) {
    app.mcp.free_scroll = false;
    let rows = rows::rows(&app.mcp);
    let next = if down {
        (app.mcp.selected + 1..rows.len()).find(|index| rows[*index].selectable())
    } else {
        (0..app.mcp.selected)
            .rev()
            .find(|index| rows[*index].selectable())
    };
    if let Some(index) = next {
        app.mcp.selected = index;
    } else {
        super::search::focus(app);
    }
}

/// Lines one wheel notch scrolls the option list (Textual's scroll step).
const WHEEL_STEP: usize = 2;

/// The wheel scrolls the viewport without moving the highlight, until the next
/// keyboard navigation pulls it back into view.
pub fn wheel(app: &mut App, down: bool) {
    app.mcp.free_scroll = true;
    app.mcp.scroll = if down {
        app.mcp.scroll.saturating_add(WHEEL_STEP)
    } else {
        app.mcp.scroll.saturating_sub(WHEEL_STEP)
    };
}

/// Mouse press: highlight the option under the cursor (Textual `OptionList`).
pub fn press(app: &mut App, at: (u16, u16)) {
    if app.mcp.viewing_name.is_none() && app.mcp.search.area.contains(at.into()) {
        super::search::focus(app);
        return;
    }
    if let Some(row) = row_at(app, at) {
        app.mcp.search.focused = false;
        app.mcp.selected = row;
    }
}

/// Mouse release: a click on a source opens its tools, like Enter does.
pub fn release(app: &mut App, client: &Arc<Client>, at: (u16, u16)) {
    let Some(row) = row_at(app, at) else {
        return;
    };
    if row != app.mcp.selected {
        return;
    }
    if matches!(rows::rows(&app.mcp).get(row), Some(Row::Source(_))) {
        select(app, client);
    }
}

/// The selectable row under a screen cell, from the last render's geometry.
fn row_at(app: &App, at: (u16, u16)) -> Option<usize> {
    let area = app.mcp.list_area;
    let inside = at.0 >= area.x && at.0 < area.right() && at.1 >= area.y && at.1 < area.bottom();
    if !app.mcp.open || !inside {
        return None;
    }
    let line = app.mcp.scroll + (at.1 - area.y) as usize;
    let row = *app.mcp.line_rows.get(line)?;
    rows::rows(&app.mcp)
        .get(row)
        .is_some_and(|row| row.selectable())
        .then_some(row)
}

/// `d`/`e` on the highlighted source or tool (Python `_set_highlighted_disabled`).
pub fn set_disabled(app: &mut App, client: &Arc<Client>, disabled: bool) {
    let Some(row) = rows::rows(&app.mcp).into_iter().nth(app.mcp.selected) else {
        return;
    };
    let (kind, name, tool_name) = match row {
        Row::Source(row) => {
            let status = if disabled {
                MCPSourceStatus::Disabled
            } else {
                MCPSourceStatus::Enabled
            };
            let Some(source) = source_mut(app, &row.name, Some(row.kind)) else {
                return;
            };
            source.status = status;
            (row.kind, row.name, None)
        }
        Row::Tool(row) => {
            let Some(name) = app.mcp.viewing_name.clone() else {
                return;
            };
            // The viewed source's own kind, which `/mcp <name>` leaves unset.
            let Some(kind) = rows::find_source(&app.mcp.state, &name, app.mcp.viewing_kind)
                .map(|source| source.kind)
            else {
                return;
            };
            let Some(source) = source_mut(app, &name, Some(kind)) else {
                return;
            };
            let Some(tool) = source.tools.iter_mut().find(|tool| tool.name == row.name) else {
                return;
            };
            tool.enabled = !disabled;
            (kind, name, Some(row.name))
        }
        _ => return,
    };
    let (method, params) = match kind {
        MCPSourceKind::Connector => (
            method::CONNECTOR_TOGGLE,
            json!({"alias": name, "disabled": disabled, "toolName": tool_name}),
        ),
        MCPSourceKind::Server => (
            method::MCP_TOGGLE,
            json!({
                "name": name,
                "source": kind.as_str(),
                "disabled": disabled,
                "toolName": tool_name,
            }),
        ),
    };
    request_state(app, client, method, params);
}

fn source_mut<'a>(
    app: &'a mut App,
    name: &str,
    kind: Option<MCPSourceKind>,
) -> Option<&'a mut MCPSourceSummary> {
    app.mcp
        .state
        .sources
        .iter_mut()
        .find(|source| source.name == name && kind.is_none_or(|kind| source.kind == kind))
}
