//! Shortcut hints under the `/mcp` option list (Python `MCPApp` help constants).

use crate::app::App;
use crate::mcp::rows::{self, Row};

const LIST_VIEW_HELP_TOOLS: &[(&str, &str)] = &[
    ("↑↓/jk", " Navigate  "),
    ("Enter", " Show tools  "),
    ("d", " Disable  "),
    ("e", " Enable  "),
    ("r", " Refresh  "),
    ("Esc", " Close"),
];
const LIST_VIEW_HELP_AUTH: &[(&str, &str)] = &[
    ("↑↓/jk", " Navigate  "),
    ("Enter", " Connect  "),
    ("d", " Disable  "),
    ("e", " Enable  "),
    ("r", " Refresh  "),
    ("Esc", " Close"),
];
const DETAIL_VIEW_HELP: &[(&str, &str)] = &[
    ("↑↓/jk", " Navigate  "),
    ("d", " Disable  "),
    ("e", " Enable  "),
    ("r", " Refresh  "),
    ("Backspace", " Back  "),
    ("Esc", " Close"),
];
const DETAIL_VIEW_HELP_NO_TOOLS: &[(&str, &str)] = &[
    ("↑↓/jk", " Navigate  "),
    ("r", " Refresh  "),
    ("Backspace", " Back  "),
    ("Esc", " Close"),
];

/// The hint for the current view: list or detail, with or without tools.
pub fn help_text(app: &App) -> &'static [(&'static str, &'static str)] {
    let rows = rows::rows(&app.mcp);
    if app.mcp.viewing_name.is_some() {
        let has_tools = rows.iter().any(|row| matches!(row, Row::Tool(_)));
        return if has_tools {
            DETAIL_VIEW_HELP
        } else {
            DETAIL_VIEW_HELP_NO_TOOLS
        };
    }
    match rows.get(app.mcp.selected) {
        Some(Row::Source(row)) if row.needs_auth => LIST_VIEW_HELP_AUTH,
        _ => LIST_VIEW_HELP_TOOLS,
    }
}
