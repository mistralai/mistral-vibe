//! Remote-project effects use the existing bounded command-result channel.

use std::sync::Arc;

use serde::de::DeserializeOwned;
use serde_json::{json, Value};

use super::{Event, Reply, State, MAX_PROJECTS};
use crate::app::App;
use crate::commands::CommandEvent;
use crate::input::deliver;
use crate::server::Client;

pub(super) enum Operation {
    Open,
    LoadMore,
    Select(String),
    Create { name: String, branch: String },
    Unlink,
    Cancel,
}

impl Operation {
    fn method(&self) -> &'static str {
        match self {
            Self::Open => "vibeCode/projects/open",
            Self::LoadMore => "vibeCode/projects/loadMore",
            Self::Select(_) => "vibeCode/projects/select",
            Self::Create { .. } => "vibeCode/projects/create",
            Self::Unlink => "vibeCode/projects/unlink",
            Self::Cancel => "vibeCode/projects/cancel",
        }
    }
}

pub(super) fn start(app: &mut App, client: &Arc<Client>, operation: Operation) {
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.command_tx.clone())
    else {
        return;
    };
    if app.vibe_code_project.pending {
        return;
    }
    let opening = matches!(&operation, Operation::Open);
    if !opening && app.vibe_code_project.session_id != session_id {
        app.vibe_code_project = State::default();
        return;
    }
    let mut params = json!({"sessionId": session_id, "pickerId": app.vibe_code_project.picker_id});
    let label = match &operation {
        Operation::Open => {
            params = json!({"sessionId": session_id, "purpose": "configure", "prompt": null});
            Some("Loading Vibe Code projects")
        }
        Operation::Create { name, branch } => {
            params["name"] = json!(name);
            params["defaultBranch"] = json!(branch);
            Some("Creating project")
        }
        Operation::Select(id) => {
            params["projectId"] = json!(id);
            None
        }
        Operation::LoadMore => Some("Loading more projects"),
        Operation::Unlink | Operation::Cancel => None,
    };
    if let Some(label) = label {
        app.begin_command_loading();
        app.view.loading.set_label(label);
    }
    if opening {
        app.vibe_code_project.session_id = session_id.clone();
    }
    app.vibe_code_project.pending = true;
    let pending = app.commit_started();
    let client = client.clone();
    let picker_id = app.vibe_code_project.picker_id.clone();
    let cancelling = matches!(operation, Operation::Cancel);
    tokio::spawn(async move {
        let event = match client.request(operation.method(), params).await {
            Ok(value) => response(operation, value).unwrap_or_else(Event::Error),
            Err(error) => Event::Error(
                match error.downcast_ref::<crate::server::RequestFailure>() {
                    Some(crate::server::RequestFailure::Rpc(error)) => error.message.clone(),
                    _ => error.to_string(),
                },
            ),
        };
        let event = match event {
            Event::Error(error) if cancelling => Event::CancelFailed(error),
            event => event,
        };
        deliver(
            Some(tx),
            CommandEvent::RemoteProject(Box::new(Reply {
                session_id,
                picker_id,
                event,
            })),
            &pending,
        )
        .await;
    });
}

fn response(operation: Operation, value: Value) -> Result<Event, String> {
    let event = match operation {
        Operation::Open => Event::Opened(decode(value)?),
        Operation::LoadMore => Event::Loaded(decode(value)?),
        Operation::Select(_) => Event::Selected(decode(value)?),
        Operation::Create { .. } => Event::Created(decode(value)?),
        Operation::Unlink => Event::Unlinked,
        Operation::Cancel => Event::Cancelled,
    };
    let view = match &event {
        Event::Opened(r) => Some(&r.view),
        Event::Loaded(r) => Some(&r.view),
        Event::Selected(r) | Event::Created(r) => Some(&r.view),
        _ => None,
    };
    if view.is_some_and(|v| v.state.projects.len() > MAX_PROJECTS) {
        return Err(format!(
            "Project list exceeds the {MAX_PROJECTS} project limit."
        ));
    }
    Ok(event)
}

fn decode<T: DeserializeOwned>(value: Value) -> Result<T, String> {
    serde_json::from_value(value).map_err(|error| format!("Invalid project response: {error}"))
}
