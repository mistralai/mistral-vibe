//! Shortcut hints under the `/mcp` option list (Python `MCPApp` help constants).

use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::mcp::rows::{self, Row};
use crate::search_field;

const LIST_VIEW_HELP_TOOLS: &[Hint] = &[
    hints::NAVIGATE,
    (key::ENTER, action::SHOW_TOOLS),
    ("d", action::DISABLE),
    ("e", action::ENABLE),
    ("r", action::REFRESH),
    hints::SEARCH,
    hints::CLOSE,
];
const LIST_VIEW_HELP_AUTH: &[Hint] = &[
    hints::NAVIGATE,
    (key::ENTER, action::CONNECT),
    ("d", action::DISABLE),
    ("e", action::ENABLE),
    ("r", action::REFRESH),
    hints::SEARCH,
    hints::CLOSE,
];
const DETAIL_VIEW_HELP: &[Hint] = &[
    hints::NAVIGATE,
    ("d", action::DISABLE),
    ("e", action::ENABLE),
    ("r", action::REFRESH),
    hints::BACK,
];
const DETAIL_VIEW_HELP_NO_TOOLS: &[Hint] = &[hints::NAVIGATE, ("r", action::REFRESH), hints::BACK];

/// The hint for the current view: list or detail, with or without tools.
pub fn help_text(app: &App) -> Vec<Hint> {
    let rows = rows::rows(&app.mcp);
    if app.mcp.viewing_name.is_some() {
        let has_tools = rows.iter().any(|row| matches!(row, Row::Tool(_)));
        let help = if has_tools {
            DETAIL_VIEW_HELP
        } else {
            DETAIL_VIEW_HELP_NO_TOOLS
        };
        return help.to_vec();
    }
    let help = match rows.get(app.mcp.selected) {
        Some(Row::Source(row)) if row.awaits_auth => LIST_VIEW_HELP_AUTH,
        _ => LIST_VIEW_HELP_TOOLS,
    };
    let search = &app.mcp.search;
    search_field::hints(search.focused, !search.query.is_empty(), help)
}
