//! Local search editing and focus for the MCP source list.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Rect;

use crate::app::App;
use crate::{chat_input, keymap};

use super::{reconcile_selection, rows};

/// Ignore excess input beyond this UTF-8 byte bound.
pub const MAX_QUERY_BYTES: usize = 1024;

#[derive(Default)]
pub struct Search {
    pub query: String,
    pub cursor: usize,
    pub anchor: Option<usize>,
    pub focused: bool,
    pub area: Rect,
    pub recorded: bool,
}

pub fn focus(app: &mut App) {
    if app.mcp.viewing_name.is_some() {
        return;
    }
    app.mcp.search.focused = true;
    app.mcp.scroll = 0;
    app.mcp.free_scroll = false;
}

fn focus_list(app: &mut App, last: bool) {
    let rows = rows::rows(&app.mcp);
    let selected = if last {
        rows.iter().rposition(|row| row.selectable())
    } else {
        rows.iter().position(|row| row.selectable())
    };
    if let Some(selected) = selected {
        app.mcp.selected = selected;
        app.mcp.search.focused = false;
        app.mcp.free_scroll = false;
    }
}

pub fn handle_key(app: &mut App, key: KeyEvent) -> bool {
    if app.mcp.viewing_name.is_some() {
        return false;
    }
    if !app.mcp.search.focused {
        if key.modifiers.is_empty() && matches!(key.code, KeyCode::Char('/') | KeyCode::Left) {
            focus(app);
            return true;
        }
        return false;
    }
    match key.code {
        KeyCode::Esc => app.mcp.search.focused = false,
        KeyCode::Up => focus_list(app, true),
        KeyCode::Down | KeyCode::Enter | KeyCode::Tab => focus_list(app, false),
        _ => {
            if let Some(action) = keymap::action_for(&key) {
                if edit(&mut app.mcp.search, action) {
                    query_changed(app);
                }
            }
        }
    }
    true
}

fn edit(search: &mut Search, action: chat_input::Action) -> bool {
    if let chat_input::Action::Insert(ch) = action {
        if ch.is_control() {
            return false;
        }
        let selected = chat_input::selection_range(&search.query, search.cursor, search.anchor)
            .map_or(0, |(start, end)| end - start);
        if search.query.len() - selected + ch.len_utf8() > MAX_QUERY_BYTES {
            return false;
        }
    }
    chat_input::apply(
        &action,
        &mut search.query,
        &mut search.cursor,
        &mut search.anchor,
    );
    action.is_edit()
}

fn query_changed(app: &mut App) {
    app.mcp.selected = 0;
    app.mcp.scroll = 0;
    app.mcp.free_scroll = false;
    reconcile_selection(app);
}

pub fn paste(app: &mut App, text: &str) {
    if !app.mcp.search.focused || app.mcp.viewing_name.is_some() {
        return;
    }
    for ch in text
        .chars()
        .take(MAX_QUERY_BYTES)
        .filter(|ch| !ch.is_control())
    {
        edit(&mut app.mcp.search, chat_input::Action::Insert(ch));
    }
    query_changed(app);
}
