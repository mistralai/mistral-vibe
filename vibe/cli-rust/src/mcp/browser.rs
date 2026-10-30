//! Keyboard actions of the open `/mcp` browser.

use std::sync::Arc;

use crate::server::Client;

use super::rows::{self, Row, SourceAction};
use super::{reconcile_selection, Event};
use crate::app::App;
use crate::option_list::Click;
use crate::selection::RegionId;
use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};

/// Esc: close the browser.
pub fn close(app: &mut App) {
    app.mcp.open = false;
    app.mcp.search = crate::search_field::Search::default();
    app.mcp.viewing_name = None;
    app.mcp.viewing_kind = None;
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
    request_action(app);
}

/// The detail view asks to start the viewed source's pending action.
pub(super) fn request_action(app: &mut App) {
    if viewing_action(app).is_none() {
        return;
    }
    let Some(tx) = app.events.clone() else {
        return;
    };
    app.commit_started();
    if tx.try_send(Event::ActionRequested.into()).is_err() {
        app.commit_finished();
    }
}

/// Start the viewed source's pending action, unless the user left that view meanwhile.
pub(super) fn hand_off(app: &mut App, client: &Arc<Client>) {
    if !app.mcp.open {
        return;
    }
    let Some((name, action)) = viewing_action(app) else {
        return;
    };
    match action {
        SourceAction::McpOAuth => crate::mcp_oauth::open(app, client, name),
        SourceAction::ConnectorAuth => crate::connector_auth::open(app, client, name),
        SourceAction::Connect => super::connect::start(app, client, name),
    }
}

/// The viewed source's pending action; a connector already connecting has none.
fn viewing_action(app: &App) -> Option<(String, SourceAction)> {
    let source = rows::viewing_source(&app.mcp)?;
    if app.mcp.connecting.as_ref() == Some(&source.name) {
        return None;
    }
    let action = rows::pending_action(&app.mcp.state, source)?;
    Some((source.name.clone(), action))
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
