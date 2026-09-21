//! The app-server prompt queue: accepted prompts, their edits and removals.

mod edit;
mod events;
mod images;
mod prompt;
mod replacement;
mod requests;
mod selection;
mod snapshot;
mod state;
mod steering;

pub use edit::replace_selected;
pub use events::{apply_event, QueueEvent, ReplacementOutcome};
pub use images::merge_edit_images;
pub use requests::{enqueue_prompt, flush_pending, pop_last, remove_selected, resume};
pub use selection::{
    confirm_consumed_edit, edit_selected, end_edit, enter, exit, finish_consumed_edit,
    handle_selection_key, is_available, select_newer, select_older,
};
pub use snapshot::sync;
pub use steering::{
    defer_prompt, history_added as steering_history_added, mutation_in_flight, reconcile_snapshot,
    steer_pending,
};

use tokio::sync::mpsc::Sender;

use crate::app::App;
use crate::server::ImageAttachment;

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
    /// Whether this prompt's current content is part of the server queue item.
    pub sent: bool,
    /// Whether any revision was sent or is being sent under this message id.
    pub ever_sent: bool,
    /// Desired group revision containing this prompt.
    pub revision: u64,
    /// Whether one replace request for this group is in flight.
    pub replacing: bool,
}

impl QueueItem {
    fn mark_sent(&mut self) {
        self.sent = true;
        self.ever_sent = true;
    }
}

#[derive(Clone, Copy)]
enum ConsumedEdit {
    AwaitingConfirmation { position: usize },
    Confirmed { position: usize },
}

/// Render and edit the app server's accepted prompt queue (Python `QueueController`).
#[derive(Default)]
pub struct QueueController {
    /// Queued prompts in FIFO order; the oldest is promoted first.
    pub items: Vec<QueueItem>,
    /// Queue item currently being atomically transferred into the active turn.
    pub(crate) steering: Option<String>,
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
    /// A selected edit promoted while typing, plus its former FIFO position.
    consumed_edit: Option<ConsumedEdit>,
    /// Chat input saved when queue mode opened, restored when it exits.
    pub draft: String,
    /// Queue ids whose turn started before their enqueue answer landed.
    started: Vec<String>,
    /// Server ids of consumed queue items: new prompts never merge into them.
    consumed: Vec<String>,
    /// Monotonic desired-state revision for merged queue items.
    next_revision: u64,
    /// Enqueue answers, applied on the main thread.
    pub tx: Option<Sender<QueueEvent>>,
}

/// Cap for late enqueue-answer ids and consumed server queue ids.
const QUEUE_ID_HISTORY: usize = 8;

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
        take_id(&mut self.started, queue_item_id)
    }

    fn remember_started(&mut self, queue_item_id: String) {
        remember_id(&mut self.started, queue_item_id);
    }

    /// Mark the whole server item consumed so later prompts cannot merge into it.
    pub(super) fn remember_consumed(&mut self, server_message_id: String) {
        remember_id(&mut self.consumed, server_message_id);
    }

    pub(super) fn is_consumed(&self, server_message_id: &str) -> bool {
        self.consumed.iter().any(|known| known == server_message_id)
    }

    /// Forget every queued prompt after a server-side session reset.
    pub fn clear(&mut self) {
        self.items.clear();
        self.started.clear();
        self.consumed.clear();
        self.steering = None;
        self.steer_after_replace = false;
        self.deferred_prompt = None;
        self.selected = None;
        self.editing = false;
        self.consumed_edit = None;
        self.draft.clear();
        self.paused = false;
    }
}

/// A queued prompt was promoted (Python `QueueController.turn_started`).
pub fn turn_started(
    app: &mut App,
    client: &std::sync::Arc<crate::server::Client>,
    queue_item_id: &str,
) -> bool {
    let was_steering = steering::turn_started(app, queue_item_id);
    let Some(server_message_id) = app
        .queue
        .items
        .iter()
        .find(|item| item.queue_item_id.as_deref() == Some(queue_item_id))
        .map(|item| item.server_message_id.clone())
    else {
        app.queue.remember_started(queue_item_id.to_owned());
        return false;
    };
    app.queue.remember_consumed(server_message_id.clone());
    if app.queue.replacement_in_flight(&server_message_id) {
        events::retire_group(app, &server_message_id);
    } else {
        events::consume_group(app, client, &server_message_id, &[]);
    }
    if was_steering {
        steering::flush_deferred(app, client);
    }
    true
}

fn take_id(ids: &mut Vec<String>, id: &str) -> bool {
    let Some(index) = ids.iter().position(|known| known == id) else {
        return false;
    };
    ids.remove(index);
    true
}

fn remember_id(ids: &mut Vec<String>, id: String) {
    if ids.iter().any(|known| known == &id) {
        return;
    }
    if ids.len() >= QUEUE_ID_HISTORY {
        ids.remove(0);
    }
    ids.push(id);
}

/// Drop one item and keep queue mode pointing at a prompt that still exists.
pub(crate) fn drop_item(app: &mut App, index: usize) -> QueueItem {
    let item = app.queue.items.remove(index);
    if app.queue.selected.as_deref() == Some(item.message_id.as_str()) {
        if app.queue.editing {
            app.queue.consumed_edit = Some(ConsumedEdit::AwaitingConfirmation { position: index });
        } else {
            selection::reselect(app, index);
        }
    }
    item
}
