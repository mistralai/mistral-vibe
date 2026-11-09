//! Queue edit replacement.

use std::sync::Arc;

use crate::app::App;
use crate::server::Client;
use crate::transcript::local;

use super::replacement::replace_group;
use super::requests::enqueue_prompt;

/// Save the edited prompt, then leave queue mode so the normal
/// bindings (Enter steers, Esc interrupts) resume. The saved edit is no longer
/// in the composer, so `exit` must restore the draft, not clear the input.
pub fn save_edit(app: &mut App, client: &Arc<Client>, text: String) {
    replace_selected(app, client, text);
    app.queue.editing = false;
    super::selection::exit(app);
    crate::completion_manager::input_changed(app);
}

pub fn replace_selected(app: &mut App, client: &Arc<Client>, text: String) {
    let Some(index) = app.queue.selected_position() else {
        enqueue_prompt(app, client, text);
        return;
    };
    let message_id = app.queue.items[index].message_id.clone();
    let server_message_id = app.queue.items[index].server_message_id.clone();
    let waiting_for_id = app.queue.items[index].queue_item_id.is_none();
    app.queue.items[index].ever_sent |= app.queue.items[index].sent;
    app.queue.items[index].text = text.clone();
    app.queue.items[index].sent = false;
    app.queue.revise_group(&server_message_id);
    if waiting_for_id {
        if !app.queue.items[index].ever_sent {
            local::set_text(&mut app.view.transcript, &message_id, &text);
        }
        return;
    }
    replace_group(app, client, &server_message_id);
}
