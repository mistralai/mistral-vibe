//! Keyboard actions of the open `/mcp` browser (Python `MCPApp` actions).

use std::sync::Arc;

use crate::server::{Client, MCPSourceKind};

use super::rows::{self, Row};
use super::{add_result, reconcile_selection, Event};
use crate::app::App;

/// Esc: close the browser and mount the closed message (Python `MCPClosed`).
pub fn close(app: &mut App) {
    app.mcp.open = false;
    app.mcp.search = crate::search_field::Search::default();
    app.mcp.viewing_name = None;
    app.mcp.viewing_kind = None;
    add_result(app, "MCP and connectors closed.");
}

/// Esc in the tool list: back to the source list, highlighting its first
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

/// Enter: open the highlighted source's tools (or its auth flow), or the manage-connectors page.
pub fn select(app: &mut App) {
    let row = match rows::rows(&app.mcp).into_iter().nth(app.mcp.selected) {
        Some(Row::Source(row)) => row,
        Some(Row::Manage) => {
            if let Some(url) = &app.mcp.state.manage_connectors_url {
                crate::external_url::open(url);
            }
            return;
        }
        _ => return,
    };
    app.mcp.search.focused = false;
    app.mcp.viewing_name = Some(row.name);
    app.mcp.viewing_kind = Some(row.kind);
    app.mcp.selected = 0;
    app.mcp.scroll = 0;
    app.mcp.free_scroll = false;
    reconcile_selection(app);
    request_auth(app);
}

/// Python's detail view posts `MCPOAuthRequested` / `ConnectorAuthRequested` for a source awaiting auth.
pub(super) fn request_auth(app: &mut App) {
    if !rows::viewing_source(&app.mcp).is_some_and(rows::awaits_auth) {
        return;
    }
    let Some(tx) = app.mcp.tx.clone() else {
        return;
    };
    app.commit_started();
    if tx.try_send(Event::AuthRequested).is_err() {
        app.commit_finished();
    }
}

/// Hand the viewed source to its auth flow, unless the user left that view meanwhile.
pub(super) fn hand_off_auth(app: &mut App, client: &Arc<Client>) {
    if !app.mcp.open {
        return;
    }
    let Some(source) = rows::viewing_source(&app.mcp).filter(|source| rows::awaits_auth(source))
    else {
        return;
    };
    let (name, kind) = (source.name.clone(), source.kind);
    match kind {
        MCPSourceKind::Server => crate::mcp_oauth::open(app, client, name),
        MCPSourceKind::Connector => crate::connector_auth::open(app, client, name),
    }
}

/// Move the highlight to the next selectable row, wrapping at the ends.
pub fn navigate(app: &mut App, down: bool) {
    app.mcp.free_scroll = false;
    let rows = rows::rows(&app.mcp);
    let selectable = |index: usize| rows[index].selectable();
    let next = crate::list_nav::wrap_selectable(app.mcp.selected, rows.len(), down, selectable);
    if let Some(index) = next {
        app.mcp.selected = index;
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

/// Mouse release: a click on a source or action selects it, like Enter does.
pub fn release(app: &mut App, at: (u16, u16)) {
    let Some(row) = row_at(app, at) else {
        return;
    };
    if row != app.mcp.selected {
        return;
    }
    if matches!(
        rows::rows(&app.mcp).get(row),
        Some(Row::Source(_) | Row::Manage)
    ) {
        select(app);
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
