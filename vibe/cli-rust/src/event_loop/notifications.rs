//! Notification absorption for the steady loop.

use std::sync::Arc;

use crate::app::App;
use crate::event_handler;
use crate::server::{notification, Client, Notification};

use super::{EventSources, NOTIF_DRAIN_CAP};

/// Apply one notification and drain what fits, deferring when approval is full.
/// Returns whether any of them was more than a replay drain sentinel.
pub(super) fn absorb_notification(
    app: &mut App,
    client: &Arc<Client>,
    sources: &mut EventSources,
    first: Notification,
    deferred: &mut Option<Notification>,
) -> bool {
    let mut real = first.method != notification::REPLAY_DRAINED;
    if !event_handler::apply_notification(app, client, &first) {
        *deferred = Some(first);
    }
    let mut drained = 0;
    while drained < NOTIF_DRAIN_CAP && deferred.is_none() {
        match sources.notifications.try_recv() {
            Ok(notification) => {
                real |= notification.method != notification::REPLAY_DRAINED;
                if !event_handler::apply_notification(app, client, &notification) {
                    *deferred = Some(notification);
                }
                drained += 1;
            }
            Err(_) => break,
        }
    }
    real
}

/// Mark the backend closed; surface the crash notice when it died on us.
/// Returns whether the notice changed the transcript.
pub fn surface_server_close(app: &mut App, crashed: bool) -> bool {
    app.server_closed = true;
    if !crashed {
        return false;
    }
    // The server is gone: no turn is generating anymore, and the composer must
    // stay inert (`Failed` locks it, submit, and queue edits) until the user
    // restarts the CLI. The crash notice in the transcript carries the message.
    let during_startup = matches!(app.session.status, crate::app::Status::Starting);
    crate::program_status::reset_shell(app);
    app.set_status(crate::app::Status::Failed);
    app.session.startup_error = Some(
        if during_startup {
            "the app server exited during startup"
        } else {
            "the app server exited unexpectedly"
        }
        .to_string(),
    );
    app.terminal_notifier
        .set_error_message(app.session.startup_error.as_deref().unwrap());
    let id = format!("rs-server-closed-{}", app.view.transcript.revision());
    crate::transcript::local::add_notice(
        &mut app.view.transcript,
        &id,
        "App server closed unexpectedly. Press Ctrl+C to exit.",
    );
    true
}
