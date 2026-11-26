//! Shortcut hints under the `/mcp` option list.

use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::mcp::detail;
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
const LIST_VIEW_HELP_BLOCKED: &[Hint] = &[
    hints::NAVIGATE,
    (key::ENTER, action::WHY_BLOCKED),
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
    hints::SEARCH,
    hints::BACK,
];
const DETAIL_VIEW_HELP_NO_TOOLS: &[Hint] = &[hints::NAVIGATE, ("r", action::REFRESH), hints::BACK];

/// The hint for the current view: list or detail, with or without tools.
pub fn help_text(app: &App) -> Vec<Hint> {
    if app.mcp.viewing_name.is_some() {
        if !detail::searchable(&app.mcp) {
            return DETAIL_VIEW_HELP_NO_TOOLS.to_vec();
        }
        let search = &app.mcp.tool_search;
        return search_field::hints(search.focused, !search.query.is_empty(), DETAIL_VIEW_HELP);
    }
    let rows = rows::rows(&app.mcp);
    let help = match rows.get(app.mcp.list.selected()) {
        Some(Row::Source(row)) if row.action.is_some() => LIST_VIEW_HELP_AUTH,
        Some(Row::Source(row)) if row.blocked => LIST_VIEW_HELP_BLOCKED,
        _ => LIST_VIEW_HELP_TOOLS,
    };
    let search = &app.mcp.search;
    search_field::hints(search.focused, !search.query.is_empty(), help)
}
