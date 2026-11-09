//! OSC 7501 reports.

use base64::{engine::general_purpose::STANDARD, Engine};

pub mod detection;

pub fn supported() -> bool {
    use std::io::IsTerminal;
    cfg!(unix) && std::io::stdout().is_terminal()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProgramState {
    Idle,
    Working,
    Done,
    Error,
    Blocked(BlockedKind),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BlockedKind {
    Permission,
    Question,
}

pub fn shell_outcome(
    app: &crate::app::App,
    operation_id: &str,
    failed: bool,
) -> Option<ProgramState> {
    // _shell.py::shell_effect_state reports nonzero exits in the effect, not the RPC.
    let status = app
        .view
        .transcript
        .entry_raw(operation_id)
        .and_then(|entry| entry.pointer("/state/status"))
        .and_then(serde_json::Value::as_str);
    if app.session.shell_interrupt_requested || status == Some("cancelled") {
        return Some(ProgramState::Idle);
    }
    if failed || status == Some("failed") {
        return Some(ProgramState::Error);
    }
    (status == Some("completed")).then_some(ProgramState::Done)
}

pub fn shell_completed(app: &mut crate::app::App, operation_id: &str, failed: bool) {
    if app.session.shell_operation_id.as_deref() != Some(operation_id) {
        return;
    }
    let Some(state) = shell_outcome(app, operation_id, failed) else {
        app.session.shell_rpc_completed = true;
        return;
    };
    if state == ProgramState::Error {
        let message = app
            .view
            .transcript
            .entry_raw(operation_id)
            .and_then(|entry| entry.pointer("/state/error/message"))
            .and_then(serde_json::Value::as_str)
            .unwrap_or("Shell command failed")
            .to_owned();
        app.terminal_notifier.set_error_message(&message);
    }
    reset_shell(app);
    app.terminal_notifier.remember_completion(state);
}

pub fn shell_completed_with_error(
    app: &mut crate::app::App,
    operation_id: &str,
    error: Option<&str>,
) {
    let current = app.session.shell_operation_id.as_deref() == Some(operation_id);
    shell_completed(app, operation_id, error.is_some());
    if current {
        if let Some(error) = error {
            app.terminal_notifier.set_error_message(error);
        }
    }
}

pub fn reset_shell(app: &mut crate::app::App) {
    if app.session.shell_operation_id.take().is_some() {
        app.view.command_loading = false;
    }
    app.session.shell_started_at = None;
    app.session.shell_request_started = false;
    app.session.shell_rpc_completed = false;
    app.session.shell_interrupt_requested = false;
}

pub fn shell_history_updated(app: &mut crate::app::App) {
    if app.session.shell_rpc_completed {
        if let Some(id) = app.session.shell_operation_id.clone() {
            shell_completed(app, &id, false);
        }
    }
}

fn sanitize_message(msg: &str) -> String {
    let mut message = String::new();
    for ch in msg
        .chars()
        .map(|ch| match ch {
            '\n' | '\r' | '\t' => ' ',
            _ => ch,
        })
        .filter(|ch| !ch.is_control())
    {
        if message.len() + ch.len_utf8() > 2048 {
            break;
        }
        message.push(ch);
    }
    message
}

pub fn encode_report(state: ProgramState, msg: &str) -> String {
    let name = match state {
        ProgramState::Idle => "idle",
        ProgramState::Working => "working",
        ProgramState::Done => "done",
        ProgramState::Error => "error",
        ProgramState::Blocked(BlockedKind::Permission) => "blocked:kind=permission",
        ProgramState::Blocked(BlockedKind::Question) => "blocked:kind=question",
    };
    let mut report = format!("\x1b]7501;state={name}");
    report.push_str(":app=vibe");
    let msg = sanitize_message(msg);
    if !msg.is_empty() {
        report.push_str(":msg=");
        report.push_str(&STANDARD.encode(msg));
    }
    report.push_str("\x1b\\");
    report
}
