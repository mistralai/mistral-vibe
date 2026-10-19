//! Compute terminal status from the current application facts.

use super::{Instant, NotificationContext, Outcome};
use crate::app::{App, Status};
use crate::program_status::{BlockedKind, ProgramState};

fn blocked(app: &App) -> Option<(BlockedKind, &str)> {
    if app.approval.open {
        let message = app.approval.active.as_ref().map_or("", |callback| {
            callback.detail.effect.display.status_text.as_str()
        });
        return Some((BlockedKind::Permission, message));
    }
    app.question_app.open.then(|| {
        let message = app
            .question_app
            .questions
            .first()
            .map_or("", |question| question.question.as_str());
        (BlockedKind::Question, message)
    })
}

pub fn program_state(app: &App) -> ProgramState {
    if !matches!(app.session.status, Status::Failed) {
        if let Some((kind, _)) = blocked(app) {
            return ProgramState::Blocked(kind);
        }
        if matches!(app.session.status, Status::Generating { .. })
            || app.session.shell_operation_id.is_some()
            || (!app.queue.is_empty() && !app.queue.paused)
        {
            return ProgramState::Working;
        }
    }
    match app.terminal_notifier.outcome {
        Some(Outcome::Done) => ProgramState::Done,
        Some(Outcome::Error(_)) => ProgramState::Error,
        None => ProgramState::Idle,
    }
}

pub fn action_required(app: &mut App) {
    if blocked(app).is_some() {
        app.terminal_notifier
            .notify(NotificationContext::ActionRequired, Instant::now());
    }
}

pub fn title(app: &App) -> String {
    app.terminal_notifier.title(program_state(app))
}

pub fn take_title(app: &mut App) -> Option<String> {
    let state = program_state(app);
    app.terminal_notifier.take_title(state)
}

pub fn take_program_report(app: &mut App) -> Option<String> {
    let state = program_state(app);
    let message = blocked(app).map_or("", |(_, message)| message).to_owned();
    app.terminal_notifier.take_program_report(state, &message)
}
