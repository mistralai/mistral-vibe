//! Connector auth bottom-app: title, option list, detail static, help line.

use ratatui::layout::Rect;
use ratatui::Frame;

use super::auth_app::{self, Row, View};
use crate::app::App;
use crate::connector_auth;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let view = View {
        title: format!("Connector: {}", app.connector_auth.connector_name),
        rows: rows(app),
        selected: app.connector_auth.selected,
        detail: connector_auth::detail(app),
        help: connector_auth::help_text(app),
    };
    app.connector_auth.list_area = auth_app::draw(
        app,
        f,
        area,
        &view,
        crate::mouse::MouseTarget::ConnectorAuth,
    );
}

fn rows(app: &App) -> Vec<Row> {
    connector_auth::rows(app)
        .iter()
        .map(|row| match row {
            connector_auth::Row::Note(text) => Row::note(text),
            connector_auth::Row::Blank => Row::blank(),
            connector_auth::Row::Action {
                before, key, after, ..
            } => Row::action(vec![
                (before.clone(), false),
                (key.clone(), true),
                (after.clone(), false),
            ]),
        })
        .collect()
}
