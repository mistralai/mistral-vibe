//! Search focus and query routing for the MCP source list.

use crossterm::event::KeyEvent;

use crate::app::App;
use crate::search_field::{self, Outcome};

use super::reconcile_selection;

pub fn focus(app: &mut App) {
    if app.mcp.viewing_name.is_some() {
        return;
    }
    app.mcp.search.focused = true;
    app.mcp.scroll = 0;
    app.mcp.free_scroll = false;
}

/// Route a key through the shared search model; false when the list handles it.
pub fn handle_key(app: &mut App, key: KeyEvent) -> bool {
    if app.mcp.viewing_name.is_some() {
        return false;
    }
    match search_field::handle_key(&mut app.mcp.search, &key) {
        Outcome::Pass => false,
        Outcome::Consumed => true,
        Outcome::Filtered => {
            query_changed(app);
            true
        }
    }
}

fn query_changed(app: &mut App) {
    app.mcp.selected = 0;
    app.mcp.scroll = 0;
    app.mcp.free_scroll = false;
    reconcile_selection(app);
}

pub fn paste(app: &mut App, text: &str) {
    if app.mcp.viewing_name.is_some() || !search_field::paste(&mut app.mcp.search, text) {
        return;
    }
    query_changed(app);
}
