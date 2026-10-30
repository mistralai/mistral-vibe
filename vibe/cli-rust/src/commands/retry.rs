//! `/retry`: re-enqueue a turn that continues an interrupted model response.

use std::sync::Arc;

use serde_json::Value;

use super::event::{dispatch, CommandEvent};
use super::retry_prompt::build_retry_prompt;
use super::submission::{new_message_id, start_injected_turn};
use crate::app::{App, QueuedPrompt, Status};
use crate::server::{Client, EntryHead};

/// Silent continuations before the
/// failure surfaces as a manual `/retry` offer.
pub const MAX_INCOMPLETE_STREAM_RETRIES: u32 = 2;

/// The interrupted answer row plus the rows that
/// vanish when the retried turn's first entry lands.
pub struct RetryPresentation {
    pub assistant_id: Option<String>,
    pub transient_ids: Vec<String>,
    pub active: bool,
}

/// A retried turn's output merges into the interrupted assistant row: the new
/// entry stays hidden and its content is recomputed on top of the frozen base.
#[derive(Clone)]
pub struct RetryContinuation {
    pub dst_id: String,
    pub src_id: String,
    pub base_content: Value,
}

/// Continue an interrupted model response, optionally with extra instructions.
/// Does nothing while a turn is generating; warns when nothing was interrupted.
pub fn start_retry(app: &mut App, client: &Arc<Client>, cmd_args: &str) {
    if matches!(app.session.status, Status::Generating { .. }) {
        return;
    }
    if !app.session.can_retry {
        let text = "No interrupted response to continue.";
        let id = new_message_id();
        crate::transcript::local::add_command_warning(&mut app.view.transcript, &id, text);
        return;
    }
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    begin(app);
    app.session.can_retry = false;
    // A manual retry re-opens the incomplete-stream budget.
    app.session.incomplete_stream_retries = 0;
    // Mark busy before the injected turn lands so nothing races the retry.
    app.set_status(Status::Generating {
        since: std::time::Instant::now(),
    });
    let prompt = QueuedPrompt {
        message_id: new_message_id(),
        text: build_retry_prompt(cmd_args),
    };
    let client = client.clone();
    tokio::spawn(async move {
        // A failed turn/start never gets a turn/completed, so its event must unstick the busy state.
        let event = match start_injected_turn(client, session_id, prompt).await {
            Ok(()) => CommandEvent::RetryStarted,
            Err(error) => CommandEvent::RetryFailed {
                error: error.to_string(),
            },
        };
        let _ = tx.try_send(event);
    });
}

/// Capture the interrupted answer and arm the presentation.
/// `transient` is the error row mounted for the failure, if any. A later failure
/// keeps the captured answer and appends its error row.
pub fn offer(app: &mut App, transient: Option<String>) {
    let mut presentation = app
        .session
        .retry_presentation
        .take()
        .unwrap_or(RetryPresentation {
            assistant_id: None,
            transient_ids: Vec::new(),
            active: false,
        });
    if presentation.assistant_id.is_none() {
        presentation.assistant_id = app.session.turn_assistant_id.clone();
    }
    if let Some(id) = transient {
        presentation.transient_ids.push(id);
    }
    presentation.active = false;
    app.session.retry_presentation = Some(presentation);
}

/// `/retry` activates the armed presentation, if any.
pub fn begin(app: &mut App) {
    if let Some(presentation) = &mut app.session.retry_presentation {
        presentation.active = true;
    }
}

/// Drop the presentation, rows stay mounted.
pub fn cancel(app: &mut App) {
    app.session.retry_presentation = None;
    app.session.turn_assistant_id = None;
    app.session.can_retry = false;
}

/// The retry dispatch of an added entry: a user entry always cancels
/// the offer, and only the retried turn's first entry consumes the
/// presentation — removing the transient rows, and continuing the interrupted
/// answer when the entry is an assistant message. An inactive offer (armed by
/// a failure, before the user runs `/retry`) survives non-user entries.
pub fn on_entry_added(app: &mut App, entry: &EntryHead) {
    let Some(presentation) = app.session.retry_presentation.as_ref() else {
        return;
    };
    if !presentation.active {
        if entry.message_role() == Some("user") {
            cancel(app);
        }
        return;
    }
    let presentation = app.session.retry_presentation.take().unwrap();
    match entry.message_role() {
        // A real user entry cancels the offer; everything stays mounted.
        Some("user") => cancel(app),
        Some("assistant") => resolve(app, &presentation, true, entry.id.clone()),
        _ => resolve(app, &presentation, false, None),
    }
}

fn resolve(
    app: &mut App,
    presentation: &RetryPresentation,
    continue_assistant: bool,
    continuation_src: Option<String>,
) {
    for id in &presentation.transient_ids {
        app.view.transcript.remove(id);
    }
    let Some(dst) = presentation.assistant_id.as_deref() else {
        return;
    };
    let Some(src) = continuation_src else {
        return;
    };
    if !continue_assistant || !app.view.transcript.contains(dst) {
        return;
    }
    let Some(base) = app.view.transcript.entry_content(dst) else {
        return;
    };
    app.view.transcript.hide(&src);
    app.session.retry_continuation = Some(RetryContinuation {
        dst_id: dst.to_owned(),
        src_id: src,
        base_content: base,
    });
}

/// Track the turn's latest assistant row,
/// which the next failure's presentation captures. A continuation's hidden
/// entry keeps pointing at the merged row it feeds. A user entry starts a new
/// turn, so the previous turn's assistant row is no longer its target.
pub fn track_turn_assistant(app: &mut App, entry: &EntryHead) {
    let id = match (entry.message_role(), entry.id.clone()) {
        (Some("user"), _) => {
            app.session.turn_assistant_id = None;
            return;
        }
        (Some("assistant"), Some(id)) => id,
        _ => return,
    };
    let tracked = match app.session.retry_continuation.as_ref() {
        Some(continuation) if continuation.src_id == id => continuation.dst_id.clone(),
        _ => id,
    };
    app.session.turn_assistant_id = Some(tracked);
}

/// A truncated stream silently
/// continues while the budget lasts and the server queue is idle; the failure
/// surfaces as a manual `/retry` offer only once the budget is spent.
pub fn auto_continue_incomplete_stream(
    app: &mut App,
    client: &Arc<Client>,
    turn: &crate::server::PublicTurn,
) -> bool {
    if turn.failure() != Some("incomplete_stream") {
        return false;
    }
    if app.session.incomplete_stream_retries >= MAX_INCOMPLETE_STREAM_RETRIES {
        return false;
    }
    if app
        .queue
        .items
        .iter()
        .any(|item| item.queue_item_id.is_some())
    {
        return false;
    }
    let Some((session_id, tx)) = dispatch(app) else {
        return false;
    };
    app.session.incomplete_stream_retries += 1;
    // Silent offer + immediate begin: no error row, the answer continues.
    offer(app, None);
    app.session.can_retry = true;
    if let Some(presentation) = &mut app.session.retry_presentation {
        presentation.active = true;
    }
    app.set_status(Status::Generating {
        since: std::time::Instant::now(),
    });
    // The retry turn opens with the Retrying label.
    app.view
        .loading
        .set_label(crate::ui::loading::RETRYING_LOADING_STATUS);
    let prompt = QueuedPrompt {
        message_id: new_message_id(),
        text: build_retry_prompt(""),
    };
    let client = client.clone();
    tokio::spawn(async move {
        let event = match start_injected_turn(client, session_id, prompt).await {
            Ok(()) => CommandEvent::RetryStarted,
            Err(error) => CommandEvent::RetryFailed {
                error: error.to_string(),
            },
        };
        let _ = tx.try_send(event);
    });
    true
}
