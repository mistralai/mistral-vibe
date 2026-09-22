//! Slash-command dispatch and queued prompt submission.

use std::sync::Arc;

use crate::server::Client;
use crate::server::{
    method, ContentBlock, PreparedPrompt, TurnEnqueueParams, TurnInputEntry, TurnInterruptParams,
    TurnStartParams,
};
use serde_json::json;
use tokio::sync::mpsc;

use super::{dispatch, is_side_channel, parse};
use crate::app::{App, QueuedPrompt, Status, ToastSeverity};
use crate::input_modes::{classify, ClassifiedInput};
use crate::transcript::local;
use crate::{completion_manager, config, message_queue};

pub(super) const NOTICE_TIMEOUT_SECS: u64 = 4;
/// Toast timeout, Python `App.NOTIFICATION_TIMEOUT` (5s).
const TOAST_SECS: u64 = 5;

/// Replay slash commands deferred while the session was starting (Python
/// releases `_session_ready.wait()` and dispatches the held input).
/// Returns true if a deferred `/exit` was replayed, so the caller can quit.
pub fn flush_pending(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
) -> bool {
    // Nothing was deferred: leave input and completions untouched so a bare
    // Ready does not reopen a dismissed popup (Python dispatches nothing).
    if app.pending_commands.is_empty() {
        return false;
    }
    let pending = std::mem::take(&mut app.pending_commands);
    // Preserve text typed after the commands were deferred; replay must not
    // clear it or record it in history (history.add skips the empty string
    // that run_command's clear_and_remember sees).
    let mut saved_input = app.chat_input.full_text();
    let saved_cursor = app.chat_input.cursor;
    let mut restore_rejected = false;
    for value in pending {
        let Some(command) = parse(&value) else {
            continue;
        };
        // A queued prompt may have started a turn before deferred commands replay.
        if !is_side_channel(command) {
            if let Some(hint) = reject_hint(app) {
                if saved_input.is_empty() {
                    saved_input = value;
                    restore_rejected = true;
                }
                app.show_toast(
                    format!("Slash commands cannot be queued — {hint}"),
                    ToastSeverity::Warning,
                    TOAST_SECS,
                );
                continue;
            }
        }
        if dispatch::run_command(app, client, config_tx, command, &value) {
            return true;
        }
    }
    app.chat_input.load_full_text(saved_input);
    app.chat_input.cursor = if restore_rejected {
        0
    } else {
        saved_cursor.min(app.chat_input.input.len())
    };
    completion_manager::input_changed(app);
    false
}

/// Submit the chat input contents; true means the application should exit.
pub fn submit(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
) -> bool {
    let value = app.chat_input.full_text().trim().to_owned();
    if matches!(app.session.status, Status::Failed) {
        return false;
    }
    // A held queue takes a bare Enter as "send it now"; an active turn takes it
    // as "steer the accepted queue into this turn" (Python `_handle_paused_submit`).
    if value.is_empty() {
        if app.queue.paused {
            message_queue::resume(app, client);
        } else {
            message_queue::steer_pending(app, client);
        }
        return false;
    }
    // Editing a queued prompt: Enter saves it in place instead of queuing a new one.
    if app.queue.editing {
        if message_queue::confirm_consumed_edit(app) {
            return false;
        }
        clear_and_remember(app, &value);
        if message_queue::finish_consumed_edit(app) {
            message_queue::enqueue_prompt(app, client, value);
        } else {
            message_queue::replace_selected(app, client, value);
            message_queue::end_edit(app);
        }
        return false;
    }
    let classified = classify(&value, &app.completion.skills);
    if !matches!(&classified, ClassifiedInput::SlashCommand { .. })
        && reject_while_shell_runs(app, &value)
    {
        return false;
    }
    match classified {
        ClassifiedInput::SlashCommand { command } => {
            // Defer every slash command until the session is ready, matching
            // Python's `_dispatch_idle_input` which awaits `_session_ready` before
            // any dispatch.
            if matches!(app.session.status, Status::Starting) {
                clear_and_remember(app, &value);
                app.pending_commands.push(value);
                return false;
            }
            if !is_side_channel(command) {
                if reject_while_shell_runs(app, &value) {
                    return false;
                }
                if let Some(hint) = reject_hint(app) {
                    reject_input(
                        app,
                        &value,
                        format!("Slash commands cannot be queued — {hint}"),
                    );
                    return false;
                }
            }
            return dispatch::run_command(app, client, config_tx, command, &value);
        }
        ClassifiedInput::Skill { command, name } => {
            if message_queue::mutation_in_flight(app) {
                if message_queue::defer_prompt(app, command) {
                    clear_and_remember(app, &value);
                    super::skill::record_usage(app, client, name);
                }
            } else {
                clear_and_remember(app, &value);
                super::skill::record_usage(app, client, name);
                message_queue::enqueue_prompt(app, client, command);
                message_queue::resume(app, client);
            }
        }
        ClassifiedInput::Bash { command } => {
            if let Some(hint) = reject_hint(app) {
                reject_input(
                    app,
                    &value,
                    format!("Shell commands cannot be queued — {hint}"),
                );
            } else {
                super::shell::run(app, client, command, &value);
            }
        }
        ClassifiedInput::EmptyBash => {
            if reject_hint(app).is_some() {
                remember(app, &value);
            } else {
                clear_and_remember(app, &value);
            }
            local::add_command_error(
                &mut app.view.transcript,
                &new_message_id(),
                "No command provided after '!'",
            );
        }
        ClassifiedInput::Prompt { text } => {
            if message_queue::mutation_in_flight(app) {
                if message_queue::defer_prompt(app, text) {
                    clear_and_remember(app, &value);
                }
            } else {
                clear_and_remember(app, &value);
                message_queue::enqueue_prompt(app, client, text);
                message_queue::resume(app, client);
            }
        }
    }
    false
}

fn reject_while_shell_runs(app: &mut App, value: &str) -> bool {
    if app.session.shell_operation_id.is_none() && app.session.pending_shell.is_none() {
        return false;
    }
    reject_input(
        app,
        value,
        "Input cannot be queued while a shell command is pending or running — wait for it to finish."
            .to_owned(),
    );
    true
}

fn reject_input(app: &mut App, value: &str, message: String) {
    app.show_toast(message, ToastSeverity::Warning, TOAST_SECS);
    remember(app, value);
    app.chat_input.cursor = 0;
    completion_manager::input_changed(app);
}

/// Why a non-side-channel command cannot run now (Python `_REJECT_HINT_*`).
fn reject_hint(app: &App) -> Option<&'static str> {
    if app.queue.paused {
        return Some("clear the queue first or remove this input.");
    }
    // Compaction runs in Python's `_agent_task`, so it counts as a busy job too.
    if matches!(app.session.status, Status::Generating { .. })
        || app.compacting
        || !app.queue.is_empty()
    {
        return Some("wait for the current job to finish.");
    }
    None
}

pub(super) fn clear_and_remember(app: &mut App, value: &str) {
    app.chat_input.clear();
    completion_manager::input_changed(app);
    remember(app, value);
}

fn remember(app: &mut App, value: &str) {
    app.chat_input.history.add(value);
    app.chat_input.history.reset_navigation();
    app.chat_input.history.persist();
}

/// Escape while a turn runs (Python `_interrupt_turn`): cancel the active turn,
/// drop the loading spinner, and mount the local interrupt marker. A spinner the
/// server has not promoted yet has no turn id: drop it locally, without an RPC.
pub fn interrupt_turn(app: &mut App, client: &Arc<Client>) {
    app.set_status(Status::Ready);
    local::add_interrupt(&mut app.view.transcript, &new_message_id());
    let (Some(session_id), Some(turn_id)) = (
        app.session.session_id.clone(),
        app.session.active_turn_id.take(),
    ) else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let params = TurnInterruptParams {
            session_id,
            expected_turn_id: turn_id,
        };
        if let Ok(value) = serde_json::to_value(params) {
            let _ = client.request(method::TURN_INTERRUPT, value).await;
        }
    });
}

/// Prepare the prompt (mentions, auto-title) then start the turn, as Python's
/// `_send_prompt` does before `session.start_turn`. When `injected` is true,
/// use `turn/start` with `injected=true` so the projection suppresses the
/// user-message entry (Python `session.act(injected=True)`).
pub(super) async fn start_turn(client: Arc<Client>, session_id: String, prompt: QueuedPrompt) {
    let _ = start_turn_with(client, session_id, prompt, false).await;
}

/// Like `start_turn` but marks the prompt as injected, matching Python's
/// `_retry` → `session.act(injected=True)`. Returns the request outcome so the
/// caller can recover the busy state when the RPC never reaches the server.
pub(super) async fn start_injected_turn(
    client: Arc<Client>,
    session_id: String,
    prompt: QueuedPrompt,
) -> anyhow::Result<()> {
    start_turn_with(client, session_id, prompt, true).await
}

async fn start_turn_with(
    client: Arc<Client>,
    session_id: String,
    prompt: QueuedPrompt,
    injected: bool,
) -> anyhow::Result<()> {
    if injected {
        let params = TurnStartParams {
            idempotency_key: Some(prompt.message_id.clone()),
            session_id,
            message: vec![ContentBlock::Text {
                text: prompt.text.clone(),
            }],
            injected: true,
            client_user_message_id: Some(prompt.message_id),
            auto_title: None,
            user_display_content: None,
            mention_stats: None,
        };
        let value = serde_json::to_value(params)?;
        client.request(method::TURN_START, value).await.map(|_| ())
    } else {
        let Ok(prepared) = client
            .request(
                method::WORKSPACE_PROMPT_PREPARE,
                json!({
                    "sessionId": session_id,
                    "message": &prompt.text,
                    "titleContent": null,
                }),
            )
            .await
        else {
            return Ok(());
        };
        let prepared = PreparedPrompt::from_response(&prepared, &prompt.text);
        let params = TurnEnqueueParams {
            idempotency_key: prompt.message_id.clone(),
            session_id,
            entries: vec![TurnInputEntry {
                annotations: Default::default(),
                content: prepared.content_blocks(&prompt.text),
                entry_id: prompt.message_id,
                role: "user",
            }],
        };
        let Ok(value) = serde_json::to_value(params) else {
            return Ok(());
        };
        let _ = client.request(method::TURN_ENQUEUE, value).await;
        Ok(())
    }
}

pub fn new_message_id() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    format!("rs-{nanos:x}")
}
