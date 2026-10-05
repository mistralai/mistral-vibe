//! The project picker opened for `/teleport` (Python `teleport_pending`).

use std::sync::Arc;

use super::request::{self, Operation};
use super::State;
use crate::app::App;
use crate::server::proto_projects::PickerView;
use crate::server::Client;

pub const REMOTE_CHANGED_MESSAGE: &str = "The saved Vibe Code project link points to a different repository remote. Pick the project to use for this repository.";

/// Python `_resolve_vibe_code_project_for_teleport`: a saved link skips the picker.
pub fn open(app: &mut App, client: &Arc<Client>, prompt: Option<String>) {
    if app.vibe_code_project.pending {
        return;
    }
    app.vibe_code_project = State {
        teleport_pending: true,
        teleport_prompt: prompt,
        ..State::default()
    };
    request::start(app, client, Operation::Open);
}

/// Python `_continue_pending_teleport`: close the picker and start teleporting.
pub(super) fn continue_pending(app: &mut App, client: &Arc<Client>, project_id: String) {
    let state = std::mem::take(&mut app.vibe_code_project);
    crate::teleport::begin(
        app,
        client,
        state.picker_id,
        project_id,
        state.teleport_prompt,
    );
}

/// Reopen the picker a recovered stale link left behind, keeping the teleport pending.
pub fn show(
    app: &mut App,
    session_id: String,
    picker_id: String,
    view: PickerView,
    prompt: Option<String>,
) {
    app.vibe_code_project = State {
        open: true,
        session_id,
        picker_id,
        view: Some(view),
        teleport_pending: true,
        teleport_prompt: prompt,
        ..State::default()
    };
    app.vibe_code_project.show_picker();
}
