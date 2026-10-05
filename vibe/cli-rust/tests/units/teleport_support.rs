//! Shared fixtures for the teleport unit tests.

use std::sync::Arc;

use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::server::{Client, HistoryEntry, MessageContent};
use vibe_rs::teleport;

pub fn rows(app: &App) -> Vec<(String, String, bool)> {
    app.view
        .transcript
        .lines()
        .filter_map(|entry| match entry.entry {
            HistoryEntry::Message(message) => Some((
                message.role.clone(),
                message
                    .content
                    .iter()
                    .filter_map(|block| match block {
                        MessageContent::Text { text } => Some(text.as_str()),
                        _ => None,
                    })
                    .collect(),
                entry.entry.in_progress(),
            )),
            _ => None,
        })
        .collect()
}

pub fn running() -> App {
    let mut app = App::default();
    app.session.status = Status::Ready;
    app.session.session_id = Some("session".into());
    teleport::begin(
        &mut app,
        &stub(),
        "picker".into(),
        "project".into(),
        Some("ship it".into()),
    );
    app
}

pub fn op_id(app: &App) -> String {
    app.teleport
        .as_ref()
        .expect("teleport running")
        .operation_id
        .clone()
}

pub fn event(operation_id: &str, kind: serde_json::Value) -> serde_json::Value {
    let mut event = kind;
    event["operationId"] = json!(operation_id);
    json!({"event": event})
}

pub fn emit(app: &mut App, kind: serde_json::Value) {
    let params = event(&op_id(app), kind);
    teleport::on_event(app, &stub(), &params);
}

pub fn stub() -> Arc<Client> {
    Arc::new(Client::stub())
}

pub fn view_json(cleared: bool) -> serde_json::Value {
    json!({
        "context": {"repoUrl": "https://github.com/org/repo", "repoName": "repo", "savedLink": null},
        "state": {"projects": [], "nextCursor": null},
        "git": {"branch": "main", "defaultBranch": "main"},
        "savedProjectLinkCleared": cleared,
    })
}

pub fn view(cleared: bool) -> vibe_rs::server::proto_projects::PickerView {
    serde_json::from_value(view_json(cleared)).unwrap()
}
