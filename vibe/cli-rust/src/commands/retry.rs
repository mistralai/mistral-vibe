//! `/retry`: re-run a failed or interrupted turn; its reply replaces the rows the attempt left.

use std::sync::Arc;

use super::event::{dispatch, CommandEvent};
use super::retry_prompt::build_retry_prompt;
use super::submission::{new_message_id, start_injected_turn};
use crate::app::{App, QueuedPrompt, Status};
use crate::server::{Client, EntryHead};

/// Silent retries of a truncated stream before it surfaces as a `/retry` offer.
pub const MAX_INCOMPLETE_STREAM_RETRIES: u32 = 2;

/// Shown when `/retry` has no failed or interrupted turn to re-run.
pub const NOTHING_TO_RETRY: &str = "No failed or interrupted response to retry";

/// Rows a successful retry replaces: the discarded answer, error rows, the interrupt marker.
#[derive(Default)]
pub struct RetryPresentation {
    pub transient_ids: Vec<String>,
    /// The retry is sent: its first entry replaces the rows.
    pub active: bool,
}

/// Re-run the last failed or interrupted turn, optionally with extra instructions.
pub fn start_retry(app: &mut App, client: &Arc<Client>, cmd_args: &str) {
    if matches!(app.session.status, Status::Generating { .. }) {
        return;
    }
    if !app.session.can_retry {
        let id = new_message_id();
        crate::transcript::local::add_command_warning(
            &mut app.view.transcript,
            &id,
            NOTHING_TO_RETRY,
        );
        return;
    }
    // A manual retry re-opens the incomplete-stream budget.
    app.session.incomplete_stream_retries = 0;
    send(app, client, cmd_args);
}

/// Offer `/retry` for a turn that failed or was interrupted, adding the rows it left.
pub fn offer(app: &mut App, turn_id: Option<&str>, row: Option<String>) {
    let discarded = turn_id
        .map(|turn_id| app.view.transcript.discarded_ids(turn_id))
        .unwrap_or_default();
    for id in discarded.into_iter().chain(row) {
        track(app, id);
    }
    let presentation = app
        .session
        .retry_presentation
        .get_or_insert_with(Default::default);
    presentation.active = false;
    app.session.can_retry = true;
}

/// Track the marker Esc left on the turn it interrupts, for the next offer to replace.
pub fn track_interrupt(app: &mut App, marker: String) {
    // A live offer is for an earlier turn, not the one Esc interrupts.
    if app.session.can_retry {
        cancel(app);
    }
    track(app, marker);
}

/// Track a row the next offer replaces, without offering `/retry` before the turn ends.
fn track(app: &mut App, id: String) {
    let presentation = app
        .session
        .retry_presentation
        .get_or_insert_with(Default::default);
    if !presentation.transient_ids.contains(&id) {
        presentation.transient_ids.push(id);
    }
    presentation.active = false;
}

/// The interrupted turn will never start: forget its marker, which stays mounted.
pub fn drop_pending_interrupt(app: &mut App) {
    let pending = app
        .session
        .retry_presentation
        .as_ref()
        .is_some_and(|presentation| !presentation.active);
    if pending && !app.session.can_retry {
        cancel(app);
    }
}

/// Drop the offer; the rows stay mounted.
pub fn cancel(app: &mut App) {
    app.session.retry_presentation = None;
    app.session.can_retry = false;
}

/// A user entry ends the offer; the retried turn's first entry replaces the rows.
pub fn on_entry_added(app: &mut App, entry: &EntryHead) {
    let Some(presentation) = app.session.retry_presentation.as_ref() else {
        return;
    };
    let active = presentation.active;
    if entry.message_role() == Some("user") {
        // Esc before the turn starts tracks its marker ahead of that turn's own prompt.
        if app.session.can_retry || active {
            cancel(app);
        }
        return;
    }
    if !active {
        return;
    }
    let Some(presentation) = app.session.retry_presentation.take() else {
        return;
    };
    for id in &presentation.transient_ids {
        app.view.transcript.dismiss(id);
    }
}

/// Silently retry a truncated stream while the budget lasts and the server queue is idle.
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
    // No error row: the retried reply replaces the discarded answer.
    offer(app, turn.id.as_deref(), None);
    let sent = send(app, client, "");
    if sent {
        app.session.incomplete_stream_retries += 1;
    }
    sent
}

/// Send the retried turn, busy at once with the loader reading Retrying.
fn send(app: &mut App, client: &Arc<Client>, cmd_args: &str) -> bool {
    let Some((session_id, tx)) = dispatch(app) else {
        return false;
    };
    app.session.can_retry = false;
    if let Some(presentation) = &mut app.session.retry_presentation {
        presentation.active = true;
    }
    app.set_status(Status::Generating {
        since: crate::clock::now(),
    });
    app.view
        .loading
        .set_label(crate::ui::loading::RETRYING_LOADING_STATUS);
    let prompt = QueuedPrompt {
        message_id: new_message_id(),
        text: build_retry_prompt(cmd_args),
    };
    let client = client.clone();
    crate::work::spawn(async move {
        // A failed turn/start never gets a turn/completed, so its event must unstick the busy state.
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
