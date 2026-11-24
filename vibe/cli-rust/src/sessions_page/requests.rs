//! The page's mutating calls, each answering with one `actions::Event`.

use std::sync::Arc;

use serde_json::{json, Value};

use super::actions::Event;
use crate::app::App;
use crate::server::request_error::{describe, resume_failure_message};
use crate::server::{method, AgentConfig, Client, PublicSessionState, HISTORY_LIMIT};

fn spawn<F>(app: &App, client: &Arc<Client>, call: impl FnOnce(Arc<Client>) -> F)
where
    F: std::future::Future<Output = Event> + Send + 'static,
{
    if app.events.is_some() {
        let call = call(client.clone());
        crate::ui_event::spawn(app, async move { super::Event::Action(call.await) });
    }
}

fn state(value: &Value) -> Option<Box<PublicSessionState>> {
    serde_json::from_value(value.get("state")?.clone())
        .ok()
        .map(Box::new)
}

pub(super) fn resume(
    app: &App,
    client: &Arc<Client>,
    id: String,
    config: AgentConfig,
    ticket: u64,
) {
    spawn(app, client, |client| async move {
        let params = crate::startup::resume_params(&id, &config);
        let result = client.request(method::SESSION_RESUME, params).await;
        let answer = result.map_err(|error| resume_failure_message(&error));
        Event::Opened(
            ticket,
            answer.and_then(|value| state(&value).ok_or_else(malformed)),
        )
    });
}

pub(super) fn start(app: &App, client: &Arc<Client>, config: AgentConfig, ticket: u64) {
    spawn(app, client, |client| async move {
        let params = json!({ "agentConfig": config, "historyLimit": HISTORY_LIMIT });
        let result = client.request(method::SESSION_START, params).await;
        let answer =
            result.map_err(|error| format!("Failed to start a session: {}", describe(&error)));
        Event::Started(
            ticket,
            answer.and_then(|value| state(&value).ok_or_else(malformed)),
        )
    });
}

pub(super) fn mark_seen(app: &App, client: &Arc<Client>, id: String) {
    spawn(app, client, |client| async move {
        let params = json!({ "sessionId": id });
        if let Err(error) = client.request(method::SESSION_MARK_AS_SEEN, params).await {
            tracing::debug!(%error, "session/markAsSeen failed");
        }
        Event::Seen(id)
    });
}

pub(super) fn rename(app: &App, client: &Arc<Client>, id: String, title: String) {
    spawn(app, client, |client| async move {
        let params = json!({ "sessionId": id, "title": title });
        let result = client.request(method::SESSION_RENAME, params).await;
        let answer = result
            .map(|value| value["title"].as_str().unwrap_or(&title).to_owned())
            .map_err(|error| format!("Failed to rename session: {}", describe(&error)));
        Event::Renamed { id, answer }
    });
}

pub(super) fn archive(app: &App, client: &Arc<Client>, id: String, archived: bool) {
    spawn(app, client, |client| async move {
        let params = json!({ "sessionId": id, "archived": archived, "requireIdle": true });
        let result = client.request(method::SESSION_ARCHIVE, params).await;
        let answer = result
            .map(|value| value["archivedAt"].as_u64())
            .map_err(|error| describe(&error));
        Event::Archived { id, answer }
    });
}

fn malformed() -> String {
    "The server answered without a session state.".to_owned()
}
