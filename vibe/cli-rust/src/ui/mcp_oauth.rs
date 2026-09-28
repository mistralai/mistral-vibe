//! MCP OAuth bottom-app: title, option list, detail static, help line.

use ratatui::layout::Rect;
use ratatui::Frame;

use super::auth_app::{self, Row, View};
use crate::app::App;
use crate::mcp_oauth;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let view = View {
        title: format!("MCP Server: {}", app.mcp_oauth.server_name),
        rows: rows(app),
        selected: app.mcp_oauth.selected,
        detail: vec![(mcp_oauth::detail(app), false)],
        help: vec![(mcp_oauth::help_text(app), false)],
    };
    app.mcp_oauth.list_area =
        auth_app::draw(app, f, area, &view, crate::mouse::MouseTarget::McpOAuth);
}

fn rows(app: &App) -> Vec<Row> {
    mcp_oauth::rows(app)
        .iter()
        .map(|row| match row {
            mcp_oauth::Row::Note(text) => Row::note(text),
            mcp_oauth::Row::Blank => Row::blank(),
            mcp_oauth::Row::Action(_, text) => Row::action(vec![(text.clone(), false)]),
        })
        .collect()
}
