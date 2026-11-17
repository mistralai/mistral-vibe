//! Search focus and query routing for the MCP source list.

use crossterm::event::KeyEvent;

use crate::app::App;
use crate::search_field;

use super::reconcile_selection;

pub fn focus(app: &mut App) {
    if app.mcp.viewing_name.is_some() {
        return;
    }
    app.mcp.search.focused = true;
    app.mcp.list.reset_viewport();
}

/// Route a key through the shared search model; false when the list handles it.
pub fn handle_key(app: &mut App, key: KeyEvent) -> bool {
    if app.mcp.viewing_name.is_some() {
        return false;
    }
    search_field::handle_key(&mut app.mcp.search, &key).handled(|| query_changed(app))
}

fn query_changed(app: &mut App) {
    app.mcp.list.reset();
    reconcile_selection(app);
}

pub fn paste(app: &mut App, text: &str) {
    if app.mcp.viewing_name.is_some() || !search_field::paste(&mut app.mcp.search, text) {
        return;
    }
    query_changed(app);
}
