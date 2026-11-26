//! Search focus and query routing for the MCP source list and a source's tools.

use crossterm::event::KeyEvent;

use crate::app::App;
use crate::search_field::{self, Search};

use super::{detail, reconcile_selection};

/// The field of the current view: the source search, or the viewed source's tool search.
pub fn field(app: &mut App) -> &mut Search {
    if app.mcp.viewing_name.is_some() {
        &mut app.mcp.tool_search
    } else {
        &mut app.mcp.search
    }
}

pub fn focus(app: &mut App) {
    if !detail::searchable(&app.mcp) {
        return;
    }
    field(app).focused = true;
    app.mcp.list.reset_viewport();
}

/// Route a key through the shared search model; false when the list handles it.
pub fn handle_key(app: &mut App, key: KeyEvent) -> bool {
    if !detail::searchable(&app.mcp) {
        return false;
    }
    search_field::handle_key(field(app), &key).handled(|| query_changed(app))
}

fn query_changed(app: &mut App) {
    app.mcp.list.reset();
    reconcile_selection(app);
}

pub fn paste(app: &mut App, text: &str) {
    if !detail::searchable(&app.mcp) || !search_field::paste(field(app), text) {
        return;
    }
    query_changed(app);
}
