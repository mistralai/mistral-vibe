//! Single archive and delete, and the rows they write in the conversation.

use std::sync::Arc;

use super::model;
use super::{request, warn, Action, Confirm, Target, View};
use crate::app::{App, Severity};
use crate::commands::submission::new_message_id;
use crate::server::{Client, StorageRefreshParams, WorktreeRemoveResponse};
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

pub struct Acted {
    pub action: Action,
    pub session_id: String,
    pub outcome: Outcome,
    /// What happened to a deleted session's worktree.
    pub worktree: Worktree,
}

/// A deleted session's worktree, decided once the delete is done.
pub enum Worktree {
    None,
    /// Other saved sessions use it: never removed.
    Shared(usize),
    /// Removal asked: the server's answer, `None` when the call failed.
    Removal(Option<WorktreeRemoveResponse>),
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
    let confirm = Confirm {
        action,
        session_id: row.session.session_id.clone(),
        title: row.title().to_owned(),
    };
    app.storage.confirm = Some(confirm);
}

/// The question a confirmation asks, in Desktop's words.
pub fn question(confirm: &Confirm) -> String {
    let title = &confirm.title;
    match confirm.action {
        Action::Archive => format!("Archive \"{title}\"? Its worktree is removed once no other session uses it; uncommitted work is saved to a git ref."),
        Action::Unarchive => format!("Unarchive \"{title}\"?"),
        Action::Delete => format!("Delete \"{title}\"? The session history is deleted. Uncommitted work in its worktree is saved to a git ref."),
    }
}

/// `y`: run the confirmed action; a delete removes the worktree no other session shares.
pub(super) fn confirm(app: &mut App, client: &Arc<Client>) {
    let Some(confirm) = app.storage.confirm.take() else {
        return;
    };
    let worktree = match confirm.action {
        Action::Delete => worktree_of(app, &confirm.session_id),
        _ => None,
    };
    let verb = match confirm.action {
        Action::Archive => "Archiving",
        Action::Unarchive => "Unarchiving",
        Action::Delete => "Deleting",
    };
    app.storage.busy = Some(format!("{verb} \"{}\"…", confirm.title));
    request::act(app, client, confirm.action, confirm.session_id, worktree);
}

/// The session's worktree path, read from the snapshot.
fn worktree_of(app: &App, id: &str) -> Option<String> {
    let snapshot = app.storage.usage.snapshot.as_ref()?;
    let worktree_id = snapshot
        .sessions
        .iter()
        .find(|s| s.session_id == id)?
        .worktree_id
        .as_deref()?;
    let worktree = snapshot.worktrees.iter().find(|w| w.id == worktree_id)?;
    Some(worktree.path.clone())
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
    let text = match acted.action {
        Action::Archive => format!("Archived \"{title}\". Its worktree is removed once no other session uses it; uncommitted work is saved to a git ref."),
        Action::Unarchive => format!("Unarchived \"{title}\"."),
        Action::Delete => delete_text(&title, &acted.worktree),
    };
    status(app, &text);
    apply_locally(app, acted.action, &acted.session_id);
    let params = StorageRefreshParams {
        wait_for_session_ids: vec![acted.session_id],
        ..Default::default()
    };
    super::refresh(app, client, params);
}

/// Show the action at once, until the scan that follows replaces the numbers.
fn apply_locally(app: &mut App, action: Action, id: &str) {
    let mut usage = app.storage.usage.clone();
    if let Some(snapshot) = usage.snapshot.as_mut() {
        snapshot.archive_suggestion.session_ids.retain(|s| s != id);
        match action {
            Action::Delete => {
                snapshot.sessions.retain(|s| s.session_id != id);
                for worktree in &mut snapshot.worktrees {
                    worktree.session_ids.retain(|s| s != id);
                }
                app.storage.view = View::Panel;
            }
            Action::Archive | Action::Unarchive => {
                let row = snapshot.sessions.iter_mut().find(|s| s.session_id == id);
                if let Some(row) = row {
                    row.archived = action == Action::Archive;
                }
            }
        }
    }
    super::replace(app, usage);
}

fn delete_text(title: &str, worktree: &Worktree) -> String {
    let deleted = format!("Deleted \"{title}\".");
    match worktree {
        Worktree::None => deleted,
        Worktree::Shared(n) => {
            let sessions = plural(*n, "other session uses", "other sessions use");
            format!("{deleted} Its worktree stays: {sessions} it.")
        }
        Worktree::Removal(Some(r)) if r.outcome == "removed" => {
            format!("{deleted} Its worktree was removed.")
        }
        Worktree::Removal(Some(r)) => format!(
            "{deleted} Its worktree was kept: {}.",
            crate::worktree_exit::removal_reason(r)
        ),
        Worktree::Removal(None) => {
            format!("{deleted} Its worktree was kept: it could not be removed.")
        }
    }
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
        .unwrap_or_else(|| crate::resume_picker::short_id(id).to_owned())
}

pub(super) fn status(app: &mut App, text: &str) {
    let text = crate::notices::escape(text);
    local::add_command_status(&mut app.view.transcript, &new_message_id(), &text);
}

pub fn plural(count: usize, one: &str, many: &str) -> String {
    format!("{count} {}", if count == 1 { one } else { many })
}
