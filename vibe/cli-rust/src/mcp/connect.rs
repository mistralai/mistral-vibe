//! Enter on a no-auth connector: create its connection, then list its tools.

use std::sync::Arc;

use serde_json::{json, Value};

use super::{back, rows, Event, TOAST_SECS};
use crate::app::{App, ToastSeverity};
use crate::server::{method, Client, MCPSourceKind};

/// Connect `name`; its detail view reads `Connecting...` until the answer lands.
pub(super) fn start(app: &mut App, client: &Arc<Client>, name: String) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    app.mcp.connecting = Some(name.clone());
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let params = json!({"sessionId": session_id, "alias": name});
        match client.request_err(method::CONNECTOR_CONNECT, params).await {
            Ok(response) => Event::Connected { name, response },
            Err(error) => Event::ConnectFailed {
                name,
                message: error.message,
            },
        }
    });
}

/// Adopt the refreshed runtime; a viewed connector still unconnected goes back to the list.
pub(super) fn finished(app: &mut App, client: &Arc<Client>, name: &str, response: &Value) {
    // Adopted while still connecting, so a repaint cannot start a second connect.
    crate::event_handler::apply_response_runtime(app, response);
    settle(app, name);
    if !response.get("runtime").is_some_and(Value::is_object) {
        super::read_and_open(app, client, String::new(), false, false);
        return;
    }
    if viewing(app, name)
        && rows::viewing_source(&app.mcp).is_some_and(|source| {
            rows::pending_action(&app.mcp.state, source) == Some(rows::SourceAction::Connect)
        })
    {
        failed(
            app,
            name,
            "The connector is not ready yet. Press r to refresh in a moment.",
        );
    }
}

/// Say why `name` did not connect, leaving its `Connecting...` view if still on it.
pub(super) fn failed(app: &mut App, name: &str, message: &str) {
    settle(app, name);
    if viewing(app, name) {
        back(app);
    }
    app.show_toast(message.to_owned(), ToastSeverity::Warning, TOAST_SECS);
}

/// Clear the in-flight marker, unless a later connect of another connector owns it.
fn settle(app: &mut App, name: &str) {
    if app.mcp.connecting.as_deref() == Some(name) {
        app.mcp.connecting = None;
    }
}

fn viewing(app: &App, name: &str) -> bool {
    app.mcp.viewing_name.as_deref() == Some(name)
        && app.mcp.viewing_kind == Some(MCPSourceKind::Connector)
}
