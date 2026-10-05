//! `/teleport`: hand the session to Vibe Code Web and follow its progress.

mod request;

use std::sync::Arc;
use std::time::Instant;

use serde_json::Value;

pub use request::{Failure, Reply};

use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::question_app::QuestionSource;
use crate::server::proto_teleport::{parse_event, TeleportEvent};
use crate::server::{
    Client, QuestionChoice, UserQuestion, UserQuestionRequest, UserQuestionResult,
};
use crate::transcript::local;

pub const PUSH_LABEL: &str = "Push and continue";
pub const STALE_PROJECT_MESSAGE: &str =
    "Saved Vibe Code project is no longer available. Pick the project to use for this repository.";
const SAVED_PROJECT_STALE: &str = "saved_project_stale";
const SESSION_CHANGED: &str = "Teleport was cancelled because the session changed";

/// One running `vibeCode/teleport/start` operation (Python's `_teleport` worker).
pub struct Operation {
    pub operation_id: String,
    pub session_id: String,
    pub picker_id: String,
    pub prompt: Option<String>,
    pub message_id: String,
    pub started_at: Instant,
    pub awaiting_push: bool,
    pub cancelling: bool,
}

/// `/teleport` (Python `_teleport_command`): arguments are ignored.
pub fn command(app: &mut App, client: &Arc<Client>) {
    crate::vibe_code_project::teleport::open(app, client, None);
}

/// `&<prompt>` and `--teleport` (Python `_handle_teleport_command`).
pub fn submit(app: &mut App, client: &Arc<Client>, target: String) {
    let id = new_message_id();
    if target.is_empty() {
        local::add_message(&mut app.view.transcript, &id, "command", "/teleport");
    } else {
        local::teleport::add_teleport_prompt(&mut app.view.transcript, &id, &target);
    }
    let prompt = (!target.is_empty()).then_some(target);
    crate::vibe_code_project::teleport::open(app, client, prompt);
}

/// Python `_teleport`: mount the status row and the spinner, then start.
pub fn begin(
    app: &mut App,
    client: &Arc<Client>,
    picker_id: String,
    project_id: String,
    prompt: Option<String>,
) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let operation_id = new_message_id();
    let message_id = format!("{operation_id}-status");
    local::teleport::add_teleport_status(&mut app.view.transcript, &message_id);
    app.restart_loading();
    app.teleport = Some(Operation {
        operation_id,
        session_id,
        picker_id,
        prompt,
        message_id,
        started_at: Instant::now(),
        awaiting_push: false,
        cancelling: false,
    });
    request::start(app, client, project_id);
}

/// Whether a teleport is running without waiting on the user.
pub fn busy(app: &App) -> bool {
    app.teleport.as_ref().is_some_and(|op| !op.awaiting_push)
}

/// When the loading spinner's timer started, while it is shown.
pub fn loading_since(app: &App) -> Option<Instant> {
    app.teleport
        .as_ref()
        .filter(|op| !op.awaiting_push)
        .map(|op| op.started_at)
}

/// Reduce one `vibeCode/teleport/event` (Python `_handle_teleport_event`).
pub fn on_event(app: &mut App, client: &Arc<Client>, params: &Value) {
    let Some((operation_id, event)) = parse_event(params) else {
        return;
    };
    let Some(op) = app.teleport.as_mut() else {
        return;
    };
    if op.operation_id != operation_id {
        return;
    }
    let status = match event {
        TeleportEvent::SummarizingContext => "Summarizing context...",
        TeleportEvent::CheckingGit => "Preparing workspace...",
        TeleportEvent::Pushing => "Syncing with remote...",
        TeleportEvent::StartingWorkflow => local::teleport::TELEPORTING,
        TeleportEvent::PushRequired {
            unpushed_count,
            branch_not_pushed,
        } => {
            op.awaiting_push = true;
            ask_push_approval(app, client, unpushed_count, branch_not_pushed);
            return;
        }
        TeleportEvent::Complete { url } => {
            settle(app, Some(&url));
            return;
        }
        TeleportEvent::Failed { error } => {
            fail(app, client, Failure::from(error));
            return;
        }
    };
    let id = op.message_id.clone();
    local::teleport::set_teleport_status(&mut app.view.transcript, &id, status);
}

/// The push-approval question (Python `_ask_push_approval`).
pub fn push_question(count: u64, branch_not_pushed: bool) -> UserQuestionRequest {
    let question = if branch_not_pushed {
        "Your branch doesn't exist on remote. Push to continue?".to_owned()
    } else {
        let plural = if count == 1 { "" } else { "s" };
        format!("You have {count} unpushed commit{plural}. Push to continue?")
    };
    let option = |label: &str| QuestionChoice {
        label: label.to_owned(),
        description: String::new(),
    };
    UserQuestionRequest {
        questions: vec![UserQuestion {
            question,
            header: "Push".to_owned(),
            options: vec![option(PUSH_LABEL), option("Cancel")],
            multi_select: false,
            hide_other: true,
        }],
        footer_note: None,
    }
}

/// Decline the push while a callback question owns the input box (Python raises).
fn ask_push_approval(app: &mut App, client: &Arc<Client>, count: u64, branch_not_pushed: bool) {
    if crate::question_app::callback_active(app) {
        let declined = UserQuestionResult {
            answers: Vec::new(),
            cancelled: true,
        };
        answer_push(app, client, &declined);
        return;
    }
    let request = push_question(count, branch_not_pushed);
    app.question_app.pending = Some((QuestionSource::TeleportPush, request));
    crate::question_app::show_pending(app);
}

/// Answer the push question: only an explicit push approves it.
pub fn answer_push(app: &mut App, client: &Arc<Client>, result: &UserQuestionResult) {
    let approved = !result.cancelled
        && result
            .answers
            .first()
            .is_some_and(|answer| answer.answer == PUSH_LABEL);
    let Some(op) = app.teleport.as_mut().filter(|op| op.awaiting_push) else {
        return;
    };
    op.awaiting_push = false;
    let id = op.message_id.clone();
    local::teleport::set_teleport_status(
        &mut app.view.transcript,
        &id,
        local::teleport::TELEPORTING,
    );
    request::respond_to_push(app, client, approved);
}

/// Esc while teleporting: cancel the server operation.
pub fn interrupt(app: &mut App, client: &Arc<Client>) {
    let Some(op) = app.teleport.as_mut().filter(|op| !op.awaiting_push) else {
        return;
    };
    if !op.cancelling {
        op.cancelling = true;
        request::cancel(app, client);
    }
}

/// A replaced session drops its teleport (Python `VibeCodeResource.reset`).
pub fn reset(app: &mut App) {
    let Some(op) = end(app) else {
        return;
    };
    app.view.transcript.remove(&op.message_id);
    add_error(app, SESSION_CHANGED);
}

/// Apply a teleport RPC answer on the main thread.
pub fn apply_reply(app: &mut App, client: &Arc<Client>, reply: Reply) {
    match reply {
        Reply::Answered {
            operation_id,
            failure,
        } => {
            if let Some(failure) = failure.filter(|_| is_current(app, &operation_id)) {
                request::cancel(app, client);
                fail(app, client, failure);
            }
        }
        Reply::Cancelled {
            operation_id,
            cancelled,
        } => {
            if !is_current(app, &operation_id) {
                return;
            }
            if cancelled {
                settle(app, None);
            } else if let Some(op) = app.teleport.as_mut() {
                op.cancelling = false;
            }
        }
        Reply::Recovered {
            session_id,
            picker_id,
            prompt,
            message,
            view,
        } => match view {
            Some(view) if can_reopen_picker(app, &session_id) => {
                local::add_command_result(
                    &mut app.view.transcript,
                    &new_message_id(),
                    STALE_PROJECT_MESSAGE,
                );
                crate::vibe_code_project::teleport::show(app, session_id, picker_id, *view, prompt);
            }
            _ if app.session.session_id.as_deref() == Some(session_id.as_str()) => {
                add_error(app, &message);
            }
            _ => {}
        },
    }
}

fn is_current(app: &App, operation_id: &str) -> bool {
    app.teleport
        .as_ref()
        .is_some_and(|op| op.operation_id == operation_id)
}

/// A late recover must not take over a teleport or picker started meanwhile.
fn can_reopen_picker(app: &App, session_id: &str) -> bool {
    app.session.session_id.as_deref() == Some(session_id)
        && app.teleport.is_none()
        && !app.vibe_code_project.open
        && !app.vibe_code_project.pending
}

/// Stop following the operation, dropping its push question if still shown or pending.
fn end(app: &mut App) -> Option<Operation> {
    let op = app.teleport.take()?;
    crate::question_app::dismiss(app, &QuestionSource::TeleportPush);
    Some(op)
}

/// Python `_handle_teleport_failure`: recover a stale saved link or show the error.
fn fail(app: &mut App, client: &Arc<Client>, failure: Failure) {
    let Some(op) = end(app) else {
        return;
    };
    app.view.transcript.remove(&op.message_id);
    if failure.code.as_deref() == Some(SAVED_PROJECT_STALE) {
        request::recover(app, client, op, failure.message);
        return;
    }
    add_error(app, &failure.message);
}

fn settle(app: &mut App, url: Option<&str>) {
    if let Some(op) = end(app) {
        local::teleport::settle_teleport(&mut app.view.transcript, &op.message_id, url);
    }
}

fn add_error(app: &mut App, message: &str) {
    local::add_command_error(&mut app.view.transcript, &new_message_id(), message);
}
