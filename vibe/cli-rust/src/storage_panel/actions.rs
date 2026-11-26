//! Single archive and delete, the y/n confirmations, and the rows an archive writes in the conversation.

use std::sync::Arc;

use super::model::{self, space, Space};
use super::review::review_estimate;
use super::{request, warn, Action, Confirm, ReviewKind, Target};
use crate::app::{App, Severity};
use crate::commands::submission::new_message_id;
use crate::server::{Client, StorageRefreshParams};
use crate::transcript::local;

const OPEN_ELSEWHERE: &str = "This session is open in another app. Close it there first.";
const BUSY: &str = "This session is busy (working or open in another app).";
const CURRENT: &str = "This is the current session.";

pub enum Outcome {
    Done,
    /// Refused: the session works or another process holds it.
    Busy,
    Failed(String),
}

/// An archive or unarchive the server answered.
pub struct Acted {
    pub action: Action,
    pub session_id: String,
    pub outcome: Outcome,
}

/// `a` or `d` on the highlighted session: ask first, unless a process holds it.
pub(super) fn ask(app: &mut App, archive: bool) {
    let Some(Target::Session(row)) = super::target(app) else {
        return;
    };
    if row.locked() {
        let message = if row.current { CURRENT } else { OPEN_ELSEWHERE };
        return warn(app, message);
    }
    let action = match (archive, row.session.archived) {
        (true, true) => Action::Unarchive,
        (true, false) => Action::Archive,
        (false, _) => Action::Delete,
    };
    let frees = super::delete::row_estimate(row.session);
    app.storage.confirm = Some(Confirm::Row {
        action,
        session_id: row.session.session_id.clone(),
        title: row.title().to_owned(),
        frees,
    });
}

/// Enter in the delete or cleanup review: ask before removing anything for good.
pub(super) fn ask_review(app: &mut App) {
    let Some(snapshot) = app.storage.usage.snapshot.as_ref() else {
        return;
    };
    let review = &app.storage.review;
    let count = review
        .order
        .iter()
        .filter(|id| review.selected.contains(*id))
        .count();
    if count == 0 {
        return warn(app, "Select at least one item first.");
    }
    let frees = review_estimate(snapshot, review.kind, &review.selected);
    let kind = review.kind;
    app.storage.confirm = Some(Confirm::Review { kind, count, frees });
}

/// The question a confirmation asks, in Desktop's words; a size the server does not know is left out.
pub fn question(confirm: &Confirm) -> String {
    let text = match confirm {
        Confirm::Row { action, title, frees, .. } => match action {
            Action::Archive => format!("Archive \"{title}\"? Its worktree is removed once no other session uses it; uncommitted work is saved to a git ref."),
            Action::Unarchive => format!("Unarchive \"{title}\"?"),
            Action::Delete => format!(
                "Delete \"{title}\"? The session history is deleted. Uncommitted work in its worktree is saved to a git ref. {}",
                space(Space::Frees, *frees)
            ),
        },
        Confirm::Review { kind, count, frees } => match kind {
            ReviewKind::Cleanup => format!(
                "Remove {} in worktrees for good? {}",
                plural(*count, "item", "items"),
                space(Space::Frees, *frees)
            ),
            _ => format!(
                "Delete {}? Their history is deleted for good. {}",
                plural(*count, "archived session", "archived sessions"),
                space(Space::Frees, *frees)
            ),
        },
    };
    text.trim_end().to_owned()
}

/// `y`: run the confirmed action.
pub(super) fn confirm(app: &mut App, client: &Arc<Client>) {
    match app.storage.confirm.take() {
        Some(Confirm::Row {
            action: Action::Delete,
            session_id,
            title,
            ..
        }) => super::delete::start(app, client, Some((session_id, title))),
        Some(Confirm::Row {
            action,
            session_id,
            title,
            ..
        }) => {
            let verb = if action == Action::Archive {
                "Archiving"
            } else {
                "Unarchiving"
            };
            app.storage.busy = Some(format!("{verb} \"{title}\"…"));
            request::act(app, client, action, session_id);
        }
        Some(Confirm::Review {
            kind: ReviewKind::Cleanup,
            ..
        }) => super::leftovers::start(app, client),
        Some(Confirm::Review { .. }) => super::delete::start(app, client, None),
        None => {}
    }
}

pub(super) fn acted(app: &mut App, client: &Arc<Client>, acted: Acted) {
    app.storage.busy = None;
    let title = title_of(app, &acted.session_id);
    match acted.outcome {
        Outcome::Busy => return warn(app, BUSY),
        Outcome::Failed(error) => {
            return crate::notices::inline(app, Severity::Error, &format!("Failed: {error}"))
        }
        Outcome::Done => {}
    }
    let archived = acted.action == Action::Archive;
    let text = match archived {
        true => format!("Archived \"{title}\". Its worktree is removed once no other session uses it; uncommitted work is saved to a git ref."),
        false => format!("Unarchived \"{title}\"."),
    };
    status(app, &text);
    let mut usage = app.storage.usage.clone();
    if let Some(snapshot) = usage.snapshot.as_mut() {
        let id = &acted.session_id;
        snapshot.archive_suggestion.session_ids.retain(|s| s != id);
        snapshot.delete_suggestion.session_ids.retain(|s| s != id);
        if let Some(row) = snapshot.sessions.iter_mut().find(|s| s.session_id == *id) {
            row.archived = archived;
        }
    }
    super::replace(app, usage);
    let params = StorageRefreshParams {
        wait_for_session_ids: vec![acted.session_id],
        ..Default::default()
    };
    super::refresh(app, client, params);
}

pub(super) fn title_of(app: &App, id: &str) -> String {
    let current = app.session.session_id.as_deref();
    app.storage
        .usage
        .snapshot
        .as_ref()
        .and_then(|snapshot| {
            model::rows(snapshot, current)
                .into_iter()
                .find(|row| row.session.session_id == id)
                .map(|row| row.title().to_owned())
        })
        .unwrap_or_else(|| crate::sessions_page::short_id(id).to_owned())
}

pub(super) fn status(app: &mut App, text: &str) {
    let text = crate::notices::escape(text);
    local::add_command_status(&mut app.view.transcript, &new_message_id(), &text);
}

pub fn plural(count: usize, one: &str, many: &str) -> String {
    format!("{count} {}", if count == 1 { one } else { many })
}
