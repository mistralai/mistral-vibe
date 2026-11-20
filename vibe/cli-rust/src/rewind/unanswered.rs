//! An interrupt before any answer takes the prompt back: the turn is rewound and the prompt refills the input.

use std::sync::Arc;

use super::request::rewind;
use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::server::Client;
use crate::transcript::local;

/// Whether the interrupt about to be sent should give its prompt back rather than leave a marker.
pub fn take_back_on_interrupt(app: &mut App, unstarted: bool) -> bool {
    // An unstarted turn's own prompt still sits in the queue.
    let eligible = app.chat_input.full_text().is_empty()
        && app.queue.len() <= usize::from(unstarted)
        && (unstarted || app.view.transcript.unanswered_prompt().is_some());
    app.session.taking_back = eligible;
    eligible
}

/// The interrupted turn completed: rewind to its prompt, or mount the marker the interrupt skipped.
pub fn turn_settled(
    app: &mut App,
    client: &Arc<Client>,
    turn_id: Option<&str>,
    status: Option<&str>,
) {
    if !app.session.taking_back {
        return;
    }
    // Only a turn the interrupt ended, still empty and with nothing typed or queued since, comes back.
    let prompt = turn_id
        .filter(|_| status == Some("interrupted"))
        .filter(|_| app.chat_input.full_text().is_empty() && app.queue.is_empty())
        .and_then(|turn_id| app.view.transcript.unanswered_turn_prompt(turn_id));
    let Some(entry_id) = prompt else {
        return give_up_take_back(app);
    };
    app.session.take_back_sent = rewind(app, client, entry_id, false, true);
    if !app.session.take_back_sent {
        give_up_take_back(app);
    }
}

/// The prompt cannot come back: mount the interrupt marker the take-back skipped.
pub fn give_up_take_back(app: &mut App) {
    if !std::mem::take(&mut app.session.taking_back) {
        return;
    }
    let marker = new_message_id();
    local::add_interrupt(&mut app.view.transcript, &marker);
    // A `/retry` of the interrupted turn replaces the marker with its reply.
    crate::commands::retry::track_interrupt(app, marker);
}

/// The take-back rewind failed: the turn stays, so restore its marker and `/retry` offer.
pub fn take_back_failed(app: &mut App) {
    app.session.take_back_sent = false;
    if !app.session.taking_back {
        return;
    }
    give_up_take_back(app);
    crate::commands::retry::offer(app, None, None);
}
