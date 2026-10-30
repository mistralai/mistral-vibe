//! Shared fixtures for the teleport unit tests.

use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::teleport;

use crate::support::{stub, with_status};

pub fn running() -> App {
    let mut app = with_status(Status::Ready);
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
