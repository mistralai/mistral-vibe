//! The answers to the page's actions: adopt, update the list, or say what failed.

use std::sync::Arc;

use super::actions::{notice, settle_refusal};
use super::requests;
use crate::app::{App, Severity};
use crate::server::request_error::SESSION_BUSY_MESSAGE;
use crate::server::{Client, PublicSessionState};

/// The archived title the notice quotes, at most this wide.
const TITLE_WIDTH: usize = 40;

pub enum Event {
    /// The answer to the open or new numbered by the ticket.
    Opened(u64, Result<Box<PublicSessionState>, String>),
    Started(u64, Result<Box<PublicSessionState>, String>),
    Seen(String),
    Renamed {
        id: String,
        answer: Result<String, String>,
    },
    Archived {
        id: String,
        answer: Result<Option<u64>, String>,
    },
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    // A switch the user cancelled or replaced never takes over.
    if let Event::Opened(ticket, _) | Event::Started(ticket, _) = &event {
        if !super::actions::current(app, *ticket) {
            if app.sessions_page.busy.is_none() {
                super::switch::abandon(app);
            }
            return;
        }
    }
    let sessions = &mut app.sessions_page.sessions;
    match event {
        Event::Opened(_, Ok(state)) => {
            let id = state.session.id.clone();
            super::adopted(app, client, *state, "Resumed");
            requests::mark_seen(app, client, id);
        }
        Event::Started(_, Ok(state)) => super::adopted(app, client, *state, "Started"),
        Event::Opened(_, Err(message)) | Event::Started(_, Err(message)) => {
            super::switch::failed(app);
            // A session another Vibe holds is a refusal to wait on, like the
            // local lease check, not a failure.
            let severity = match message == SESSION_BUSY_MESSAGE {
                true => Severity::Warning,
                false => Severity::Error,
            };
            settle_refusal(app, severity, &message)
        }
        Event::Seen(id) => {
            if let Some(session) = sessions.iter_mut().find(|session| session.id == id) {
                session.is_unseen = false;
            }
        }
        Event::Renamed {
            id,
            answer: Ok(title),
        } => {
            if let Some(session) = sessions.iter_mut().find(|session| session.id == id) {
                session.title = title;
            }
            super::relayout(app);
            super::load::changed(app, client);
        }
        Event::Archived {
            id,
            answer: Ok(archived_at),
        } => {
            let session = sessions.iter_mut().find(|session| session.id == id);
            let title = session.map(|session| {
                session.archived_at = archived_at;
                crate::utils::text::ellipsize(session.label(), TITLE_WIDTH)
            });
            super::relayout(app);
            super::load::changed(app, client);
            // An archived session leaves the list: say where it went.
            if let (Some(_), Some(title)) = (archived_at, title) {
                let text = format!("Archived {title}. Press . to see archived sessions.");
                notice(app, Severity::Information, &text);
            }
        }
        Event::Renamed {
            answer: Err(message),
            ..
        }
        | Event::Archived {
            answer: Err(message),
            ..
        } => notice(app, Severity::Error, &message),
    }
}
