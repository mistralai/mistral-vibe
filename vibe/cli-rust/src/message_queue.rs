//! The app-server prompt queue: accepted prompts, their edits and removals.

mod event;
mod images;
mod prompt;
mod replacement;
mod requests;
mod selection;
mod snapshot;
mod steering;

pub use event::QueueEvent;
pub use images::merge_edit_images;
pub use requests::{
    enqueue_prompt, flush_pending, pop_last, remove_selected, replace_selected, resume,
};
pub use selection::{
    edit_selected, end_edit, enter, exit, handle_selection_key, is_available, select_newer,
    select_older,
};
pub use snapshot::sync;
pub use steering::{
    defer_prompt, history_added as steering_history_added, mutation_in_flight, reconcile_snapshot,
    steer_pending,
};

use tokio::sync::mpsc::Sender;

use crate::app::{App, Status};
use crate::server::ImageAttachment;
use crate::transcript::local;

/// A prompt the server accepted and has not promoted to a turn yet.
pub struct QueueItem {
    /// Server queue id, `None` until the enqueue answer lands.
    pub queue_item_id: Option<String>,
    /// Transcript entry id of the queued message; the UI's stable identity for
    /// this prompt, as Python tracks the `UserMessage` widget itself.
    pub message_id: String,
    /// User-entry id owned by the single merged server queue item. Later
    /// busy-time prompts keep their own UI id but share this server id.
    pub server_message_id: String,
    pub text: String,
    pub images: Vec<ImageAttachment>,
    pub replacement_pending: bool,
    /// Whether the enqueue request went out; false only while the session is
    /// still starting and there is nothing to enqueue against.
    pub sent: bool,
}

/// Render and edit the app server's accepted prompt queue (Python `QueueController`).
#[derive(Default)]
pub struct QueueController {
    /// Queued prompts in FIFO order; the oldest is promoted first.
    pub items: Vec<QueueItem>,
    /// Queue item currently being atomically transferred into the active turn.
    pub(crate) steering: Option<String>,
    /// Server message whose queue replacement request is currently in flight.
    pub(crate) replacing: Option<String>,
    /// Retry the requested steer after the latest queue contents are accepted.
    pub(crate) steer_after_replace: bool,
    /// One prompt submitted while a queue mutation is in flight.
    pub(crate) deferred_prompt: Option<String>,
    /// Whether the server holds the queue until the user resumes it.
    pub paused: bool,
    /// Highlighted prompt in queue mode, by message id, or `None` when closed.
    pub selected: Option<String>,
    /// Whether Enter loaded the highlighted prompt into the input for editing.
    pub editing: bool,
    /// Chat input saved when queue mode opened, restored when it exits.
    pub draft: String,
    /// Queue ids whose turn started before their enqueue answer landed.
    started: Vec<String>,
    /// Enqueue answers, applied on the main thread.
    pub tx: Option<Sender<QueueEvent>>,
}

/// How many promoted-before-accepted ids are remembered; the server promotes the
/// head of a FIFO queue, so one in flight at a time is the realistic case.
const STARTED_HISTORY: usize = 8;

impl QueueController {
    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn position(&self, message_id: &str) -> Option<usize> {
        self.items
            .iter()
            .position(|item| item.message_id == message_id)
    }

    /// The highlighted prompt's position, or `None` once its turn has started.
    pub fn selected_position(&self) -> Option<usize> {
        self.position(self.selected.as_deref()?)
    }

    fn take_started(&mut self, queue_item_id: &str) -> bool {
        let Some(index) = self.started.iter().position(|id| id == queue_item_id) else {
            return false;
        };
        self.started.remove(index);
        true
    }

    fn remember_started(&mut self, queue_item_id: String) {
        if self.started.len() >= STARTED_HISTORY {
            self.started.remove(0);
        }
        self.started.push(queue_item_id);
    }

    /// Forget every queued prompt after a server-side session reset (Python
    /// `clear_server_queue`); the caller has already cleared the transcript.
    pub fn clear(&mut self) {
        self.items.clear();
        self.started.clear();
        self.steering = None;
        self.replacing = None;
        self.steer_after_replace = false;
        self.deferred_prompt = None;
        self.selected = None;
        self.editing = false;
        self.draft.clear();
        self.paused = false;
    }
}

/// Bind an accepted prompt to its queue id, or drop one the server rejected.
///
/// Returns the session a deferred feedback check belongs to, if one is due:
/// Python runs the check inside turn handling, so a late-landing enqueue
/// answer must still carry the session the prompt was submitted to — a
/// mid-turn handoff (`session/compacted`) may have replaced it by then.
pub fn apply_event(
    app: &mut App,
    client: &std::sync::Arc<crate::server::Client>,
    event: QueueEvent,
) -> Option<String> {
    match event {
        QueueEvent::Accepted {
            message_id,
            queue_item_id,
            session_id,
            images,
        } => {
            let index = app.queue.position(&message_id)?;
            let server_message_id = app.queue.items[index].server_message_id.clone();
            for item in &mut app.queue.items {
                if item.server_message_id == server_message_id {
                    item.queue_item_id = Some(queue_item_id.clone());
                }
            }
            app.queue.items[index].images = images.clone();
            let text = app.queue.items[index].text.clone();
            local::set_prompt(&mut app.view.transcript, &message_id, &text, &images);
            // The turn was promoted before its answer landed: settle it now.
            if app.queue.take_started(&queue_item_id) {
                while let Some(index) = app
                    .queue
                    .items
                    .iter()
                    .position(|item| item.queue_item_id.as_deref() == Some(&queue_item_id))
                {
                    let item = drop_item(app, index);
                    local::clear_pending(&mut app.view.transcript, &item.message_id);
                }
                return Some(session_id);
            }
            if app
                .queue
                .items
                .iter()
                .filter(|item| item.server_message_id == server_message_id)
                .any(|item| !item.sent)
            {
                replacement::replace_group(app, client, &server_message_id);
            }
        }
        QueueEvent::Rejected { message_id, error } => {
            while let Some(index) = app
                .queue
                .items
                .iter()
                .position(|item| item.server_message_id == message_id)
            {
                let item = drop_item(app, index);
                app.view.transcript.remove(&item.message_id);
            }
            if let Some(error) = error {
                local::add_command_error(&mut app.view.transcript, &message_id, &error);
            }
            // The optimistic spinner has no turn behind it: go back to idle.
            if app.queue.is_empty() && app.session.active_turn_id.is_none() {
                app.set_status(Status::Ready);
            }
        }
        QueueEvent::GroupReplaced {
            server_message_id,
            message_ids,
            images,
            error,
        } => {
            replacement::settled(
                app,
                client,
                &server_message_id,
                &message_ids,
                &images,
                error.as_deref(),
            );
        }
        QueueEvent::SteerSettled {
            queue_item_id,
            accepted,
        } => steering::settled(app, client, &queue_item_id, accepted),
    }
    None
}

/// A queued prompt was promoted (Python `QueueController.turn_started`): stop
/// rendering it as queued, or remember the id until its enqueue answer lands.
pub fn turn_started(
    app: &mut App,
    client: &std::sync::Arc<crate::server::Client>,
    queue_item_id: &str,
) -> bool {
    let was_replacing = replacement::turn_started(app, queue_item_id);
    let was_steering = steering::turn_started(app, queue_item_id);
    if !app
        .queue
        .items
        .iter()
        .any(|item| item.queue_item_id.as_deref() == Some(queue_item_id))
    {
        app.queue.remember_started(queue_item_id.to_owned());
        return false;
    }
    while let Some(index) = app
        .queue
        .items
        .iter()
        .position(|item| item.queue_item_id.as_deref() == Some(queue_item_id))
    {
        let item = drop_item(app, index);
        local::clear_pending(&mut app.view.transcript, &item.message_id);
    }
    if was_replacing || was_steering {
        steering::flush_deferred(app, client);
    }
    true
}

/// Drop one item and keep queue mode pointing at a prompt that still exists.
pub(crate) fn drop_item(app: &mut App, index: usize) -> QueueItem {
    let item = app.queue.items.remove(index);
    if app.queue.selected.as_deref() == Some(item.message_id.as_str()) {
        selection::reselect(app, index);
    }
    item
}
