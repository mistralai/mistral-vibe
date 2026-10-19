//! Keyboard actions of the open `/mcp` browser.

use std::sync::Arc;

use crate::server::{Client, MCPSourceKind};

use super::rows::{self, Row};
use super::{add_result, reconcile_selection, Event};
use crate::app::App;
use crate::option_list::Click;
use crate::selection::RegionId;
use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};

/// Esc: close the browser and mount the closed message.
pub fn close(app: &mut App) {
    app.mcp.open = false;
    app.mcp.search = crate::search_field::Search::default();
    app.mcp.viewing_name = None;
    app.mcp.viewing_kind = None;
    add_result(app, "MCP and connectors closed.");
}

/// Esc in the tool list: back to the source list, highlighting its first row.
pub fn back(app: &mut App) {
    if app.mcp.viewing_name.is_none() {
        return;
    }
    app.mcp.viewing_name = None;
    app.mcp.viewing_kind = None;
    app.mcp.list.reset();
    reconcile_selection(app);
}

/// Enter: open the highlighted source's tools (or its auth flow), or the manage-connectors page.
pub fn select(app: &mut App) {
    let row = match rows::rows(&app.mcp)
        .into_iter()
        .nth(app.mcp.list.selected())
    {
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
    app.mcp.list.reset();
    reconcile_selection(app);
    request_auth(app);
}

/// The detail view asks for MCP OAuth or connector auth for a source awaiting auth.
pub(super) fn request_auth(app: &mut App) {
    if !rows::viewing_source(&app.mcp)
        .is_some_and(|source| rows::awaits_auth(&app.mcp.state, source))
    {
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
    let Some(source) =
        rows::viewing_source(&app.mcp).filter(|source| rows::awaits_auth(&app.mcp.state, source))
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
    let rows = rows::rows(&app.mcp);
    app.mcp
        .list
        .step(down, rows.len(), true, |index| rows[index].selectable());
}

/// A press on the search field focuses it; a click on the highlighted source or
/// action selects it, like Enter, once the list has focus.
pub fn mouse(app: &mut App, event: MouseEvent) {
    let at = (event.column, event.row);
    let on_search = app.mcp.viewing_name.is_none() && app.mcp.search.area.contains(at.into());
    let pressed = event.kind == MouseEventKind::Down(MouseButton::Left);
    let click =
        crate::mouse::option_gesture(app, event, RegionId::BottomApp, |app| &mut app.mcp.list);
    if on_search && pressed {
        super::search::focus(app);
        return;
    }
    if pressed && app.mcp.list.index_at(at).is_some() {
        app.mcp.search.focused = false;
    }
    let Some(Click::Confirm(row)) = click else {
        return;
    };
    if matches!(
        rows::rows(&app.mcp).get(row),
        Some(Row::Source(_) | Row::Manage)
    ) {
        select(app);
    }
}
