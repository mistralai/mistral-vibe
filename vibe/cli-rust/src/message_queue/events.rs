//! Queue request results reduced on the main thread.

use std::sync::Arc;

use crate::app::{App, Status, ToastSeverity};
use crate::server::{Client, ImageAttachment};
use crate::transcript::local;

use super::{drop_item, replacement, requests};

#[derive(Clone, Copy)]
pub enum ReplacementOutcome {
    Replaced,
    Consumed,
    Failed,
}

pub enum QueueEvent {
    Accepted {
        message_id: String,
        queue_item_id: String,
        session_id: String,
        images: Vec<ImageAttachment>,
    },
    Rejected {
        message_id: String,
        error: Option<String>,
    },
    /// A `replace_group` settled: `covered` lists the prompts that were unsent
    /// when the request left, so only they flip to sent on success.
    GroupReplaced {
        server_message_id: String,
        revision: u64,
        covered: Vec<(String, String, Vec<ImageAttachment>)>,
        /// Transcript identities delivered by this request, in merged order.
        delivered: Vec<String>,
        outcome: ReplacementOutcome,
    },
    SteerSettled {
        queue_item_id: String,
        accepted: Option<bool>,
    },
}

/// Returns the session for a deferred feedback check when a late enqueue answer
/// settles a prompt that was promoted before the answer arrived.
pub fn apply_event(app: &mut App, client: &Arc<Client>, event: QueueEvent) -> Option<String> {
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
            if app.queue.items[index].sent {
                let text = app.queue.items[index].text.clone();
                local::set_prompt(&mut app.view.transcript, &message_id, &text, &images);
            }
            if app.queue.take_started(&queue_item_id) {
                consume_group(app, client, &server_message_id, &[]);
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
            if app.queue.is_empty() && app.session.active_turn_id.is_none() {
                app.set_status(Status::Ready);
            }
        }
        QueueEvent::GroupReplaced {
            server_message_id,
            revision,
            covered,
            delivered,
            outcome,
        } => {
            app.queue.finish_replace(&server_message_id);
            match outcome {
                ReplacementOutcome::Replaced => {
                    for (message_id, text, images) in covered {
                        let Some(index) = app.queue.position(&message_id) else {
                            continue;
                        };
                        if app.queue.items[index].server_message_id == server_message_id
                            && app.queue.items[index].text == text
                        {
                            local::set_prompt(
                                &mut app.view.transcript,
                                &message_id,
                                &text,
                                &images,
                            );
                            app.queue.items[index].images = images;
                            app.queue.items[index].mark_sent();
                        }
                    }
                    if app.queue.is_consumed(&server_message_id) {
                        app.queue.steer_after_replace = false;
                        consume_group(app, client, &server_message_id, &delivered);
                    } else if app.queue.group_revision(&server_message_id) != Some(revision)
                        || app
                            .queue
                            .items
                            .iter()
                            .any(|item| item.server_message_id == server_message_id && !item.sent)
                    {
                        replacement::replace_group(app, client, &server_message_id);
                    }
                }
                ReplacementOutcome::Consumed => {
                    app.queue.steer_after_replace = false;
                    consume_group(app, client, &server_message_id, &delivered);
                }
                ReplacementOutcome::Failed => {
                    app.queue.steer_after_replace = false;
                    show_replacement_error(app);
                    if app.queue.is_consumed(&server_message_id) {
                        consume_group(app, client, &server_message_id, &delivered);
                    }
                }
            }
            super::steering::flush_deferred(app, client);
            if !app.queue.any_replacement_in_flight()
                && std::mem::take(&mut app.queue.steer_after_replace)
            {
                super::steering::steer_pending(app, client);
            }
        }
        QueueEvent::SteerSettled {
            queue_item_id,
            accepted,
        } => super::steering::settled(app, client, &queue_item_id, accepted),
    }
    None
}

fn show_replacement_error(app: &mut App) {
    app.show_toast(
        "Failed to update queued message".to_string(),
        ToastSeverity::Error,
        5,
    );
}

/// Copy unsent revisions away from a consumed server queue item.
pub(super) fn consume_group(
    app: &mut App,
    client: &Arc<Client>,
    server_message_id: &str,
    delivered: &[String],
) {
    app.queue.remember_consumed(server_message_id.to_owned());
    let mut delivered = delivered.to_vec();
    for item in app
        .queue
        .items
        .iter()
        .filter(|item| item.server_message_id == server_message_id && item.ever_sent)
    {
        if !delivered.contains(&item.message_id) {
            delivered.push(item.message_id.clone());
        }
    }
    let mut prompts = Vec::new();
    while let Some(index) = app
        .queue
        .items
        .iter()
        .position(|item| item.server_message_id == server_message_id && !item.sent)
    {
        let item = drop_item(app, index);
        if !delivered.contains(&item.message_id) {
            app.view.transcript.remove(&item.message_id);
        }
        prompts.push((item.text, item.images));
    }
    retire_group(app, server_message_id);
    local::promote_prompts(&mut app.view.transcript, &delivered);
    for (text, images) in prompts {
        requests::enqueue_prompt_with_images(app, client, text, images);
    }
}

/// Deliver the prompts committed to a consumed queue item: they ran with the
/// turn, so only their queued styling clears. Unsent ones are left to the
/// replace that owns them.
pub(super) fn retire_group(app: &mut App, server_message_id: &str) {
    let mut message_ids = Vec::new();
    while let Some(index) = app
        .queue
        .items
        .iter()
        .position(|item| item.server_message_id == server_message_id && item.sent)
    {
        let item = drop_item(app, index);
        message_ids.push(item.message_id);
    }
    local::promote_prompts(&mut app.view.transcript, &message_ids);
}
