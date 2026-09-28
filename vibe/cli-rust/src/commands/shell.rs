//! Manual `!` command dispatch through the app-server shell resource.

use std::sync::Arc;

use crate::app::{App, Status};
use crate::commands::CommandEvent;
use crate::server::{method, Client, SessionShellCommandParams, ShellCommandAction};

use super::submission::{clear_and_remember, new_message_id};

pub struct PendingShell {
    command: String,
}

pub fn run(app: &mut App, client: &Arc<Client>, command: String, value: &str) {
    if matches!(app.session.status, Status::Starting) {
        if app.session.pending_shell.is_none() {
            clear_and_remember(app, value);
            app.session.pending_shell = Some(PendingShell { command });
        }
        return;
    }
    if !matches!(app.session.status, Status::Ready) || !can_start(app) {
        return;
    }
    clear_and_remember(app, value);
    start(app, client, command);
}

pub fn flush_pending(app: &mut App, client: &Arc<Client>) {
    let Some(pending) = app.session.pending_shell.take() else {
        return;
    };
    if !can_start(app) {
        app.session.pending_shell = Some(pending);
        return;
    }
    start(app, client, pending.command);
}

fn can_start(app: &App) -> bool {
    app.session.session_id.is_some() && app.command_tx.is_some()
}

fn start(app: &mut App, client: &Arc<Client>, command: String) {
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.command_tx.clone())
    else {
        return;
    };
    app.begin_command_loading();
    app.terminal_notifier.set_running(true);
    let pending = app.commit_started();
    let operation_id = new_message_id();
    app.session.shell_operation_id = Some(operation_id.clone());
    app.session.shell_started_at = Some(std::time::Instant::now());
    app.session.shell_request_started = false;
    app.session.shell_interrupt_requested = false;
    app.view
        .loading
        .set_label(crate::ui::loading::SHELL_LOADING_STATUS);
    let client = client.clone();
    tokio::spawn(async move {
        let params = SessionShellCommandParams {
            session_id,
            command: Some(command),
            cwd: None,
            timeout_seconds: None,
            operation_id: Some(operation_id.clone()),
            action: ShellCommandAction::Run,
        };
        let error = match serde_json::to_value(params) {
            Ok(value) => client
                .request(method::SESSION_SHELL_COMMAND, value)
                .await
                .err()
                .map(|error| format!("Command failed: {error}")),
            Err(error) => Some(format!("Command failed: {error}")),
        };
        crate::input::deliver(
            Some(tx),
            CommandEvent::ShellCompleted {
                operation_id,
                error,
            },
            &pending,
        )
        .await;
    });
}

pub fn interrupt(app: &mut App, client: &Arc<Client>) {
    if app.session.shell_operation_id.is_none() {
        return;
    }
    if !app.session.shell_request_started {
        app.session.shell_interrupt_requested = true;
        return;
    }
    send_interrupt(app, client);
}

pub fn apply_started(app: &mut App, client: &Arc<Client>, operation_id: &str) {
    if app.session.shell_operation_id.as_deref() != Some(operation_id) {
        return;
    }
    app.session.shell_request_started = true;
    if std::mem::take(&mut app.session.shell_interrupt_requested) {
        send_interrupt(app, client);
    }
}

fn send_interrupt(app: &App, client: &Arc<Client>) {
    let (Some(session_id), Some(operation_id)) = (
        app.session.session_id.clone(),
        app.session.shell_operation_id.clone(),
    ) else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let params = SessionShellCommandParams {
            session_id,
            command: None,
            cwd: None,
            timeout_seconds: None,
            operation_id: Some(operation_id),
            action: ShellCommandAction::Interrupt,
        };
        if let Ok(value) = serde_json::to_value(params) {
            let _ = client.request(method::SESSION_SHELL_COMMAND, value).await;
        }
    });
}
