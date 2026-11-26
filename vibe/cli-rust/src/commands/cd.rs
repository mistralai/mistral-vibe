//! `/cd [path]`: move the current session to another folder, keeping its conversation.

use std::path::Path;
use std::sync::Arc;

use serde_json::{json, Value};

use crate::app::{App, Severity};
use crate::cwd::{self, ResolveError};
use crate::server::request_error::SESSION_BUSY_MESSAGE;
use crate::server::{method, Client, RpcError};
use crate::transcript::local;
use crate::trust_gate::{Outcome, TrustNext};
use crate::utils::paths::{collapse_home, user_home};

pub const NO_PREVIOUS: &str = "No previous directory.";
pub const CANCELLED: &str = "Directory change cancelled.";
pub const NO_SESSION: &str = "No session to move yet.";
const BUSY: &str = "Slash commands cannot be queued — wait for the current job to finish.";

/// A `session/relocate` answer for the session and folder it asked for.
pub struct Event {
    session_id: String,
    ticket: u64,
    target: String,
    result: Result<Value, RpcError>,
}

impl Event {
    pub fn new(
        session_id: String,
        ticket: u64,
        target: String,
        result: Result<Value, RpcError>,
    ) -> Self {
        Self {
            session_id,
            ticket,
            target,
            result,
        }
    }
}

pub(super) fn run(app: &mut App, client: &Arc<Client>, args: &str) {
    let cwd = app.session.cwd.clone().unwrap_or_default();
    let home = user_home().unwrap_or_default();
    let previous = app.session.previous_cwd.as_deref().map(Path::new);
    let path = match cwd::resolve(args, Path::new(&cwd), &home, previous) {
        Ok(path) => path,
        Err(ResolveError::NoPrevious) => return refuse(app, NO_PREVIOUS, "error"),
    };
    let Some(target) = cwd::existing_dir(&path) else {
        let message = format!("Not a directory: {}", path.display());
        return refuse(app, &message, "not_a_directory");
    };
    let target = target.to_string_lossy().into_owned();
    if cwd::existing_dir(Path::new(&cwd)).is_some_and(|cwd| cwd == Path::new(&target)) {
        let message = format!("Already in {}.", collapse_home(&target));
        return crate::ui::notice::show(app, Severity::Information, &message, 4);
    }
    let Some(session_id) = app.session.session_id.clone() else {
        return refuse(app, NO_SESSION, "error");
    };
    app.session.cd_ticket += 1;
    let next = TrustNext::Cd {
        target: target.clone(),
        session_id,
        ticket: app.session.cd_ticket,
    };
    crate::trust_gate::then(app, client, target, next);
}

/// Continue `/cd` once the trust gate settled for `target`; a session switched
/// away from meanwhile, or a later `/cd`, supersedes it.
pub fn after_trust(
    app: &mut App,
    client: &Arc<Client>,
    target: String,
    session_id: String,
    ticket: u64,
    outcome: Outcome,
) {
    if !current(app, &session_id, ticket) {
        return;
    }
    match outcome {
        Outcome::Trusted => relocate(app, client, target, session_id, ticket),
        Outcome::Declined => {
            crate::ui::notice::show(app, Severity::Information, CANCELLED, 4);
            crate::telemetry::session_relocation_done(app, "cancelled", Some("trust_declined"));
        }
        Outcome::Failed(message) => refuse(app, &message, "error"),
    }
}

/// Whether the `/cd` numbered `ticket` for `session_id` is still the latest:
/// the session is still attached and no later `/cd` replaced it. It gates the
/// trust gate and a failure's notice; a move the server made is followed anyway.
pub fn current(app: &App, session_id: &str, ticket: u64) -> bool {
    app.session.session_id.as_deref() == Some(session_id) && app.session.cd_ticket == ticket
}

fn relocate(app: &mut App, client: &Arc<Client>, target: String, session_id: String, ticket: u64) {
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let params = json!({ "sessionId": session_id, "cwd": target });
        let result = client.request_err(method::SESSION_RELOCATE, params).await;
        Event {
            session_id,
            ticket,
            target,
            result,
        }
    });
}

pub fn apply_event(app: &mut App, event: Event) {
    // The client moved on to another session: the footer and `/cd -` stay its own.
    if app.session.session_id.as_deref() != Some(event.session_id.as_str()) {
        return;
    }
    let state = match event.result {
        // A move the server made is the session's folder now, whichever `/cd`
        // asked for it: answers are followed as they land, so the last one wins.
        Ok(state) => state,
        // A later `/cd` replaced this one: its failure is no longer news.
        Err(_) if app.session.cd_ticket != event.ticket => return,
        Err(error) if error.is_session_busy() => {
            return refuse(app, SESSION_BUSY_MESSAGE, "refused_busy")
        }
        Err(error) if error.code() == "conflict" => return refuse(app, BUSY, "refused_busy"),
        Err(error) => {
            let outcome = match error.message.starts_with("Not a directory") {
                true => "not_a_directory",
                false => "error",
            };
            return refuse(app, &error.message, outcome);
        }
    };
    let cwd = state["state"]["session"]["cwd"]
        .as_str()
        .map_or(event.target, str::to_owned);
    let left = app
        .session
        .worktree
        .as_ref()
        .filter(|worktree| !Path::new(&cwd).starts_with(&worktree.path))
        .map(|worktree| worktree.name.clone());
    // The move's row is the relocation checkpoint the server writes into the
    // session's history: the answer's snapshot shows it now, and every reload
    // shows it again.
    cwd::follow(app, cwd.clone());
    if let Some(name) = left {
        let text = format!("Leaving worktree {name}; it will be cleaned up if it has no changes.");
        let id = crate::commands::submission::new_message_id();
        local::add_command_status(&mut app.view.transcript, &id, &text);
    }
    crate::telemetry::session_relocation_done(app, "success", None);
}

/// ``Moved from `~/a` to `~/b` ``, or ``Moved to `~/b` `` when the folder left
/// is unknown: markdown, the folders as code.
pub fn moved_text(from: Option<&str>, to: &str) -> String {
    let from = from.filter(|from| !from.is_empty() && *from != to);
    let to = collapse_home(to);
    match from {
        Some(from) => format!("Moved from `{}` to `{to}`", collapse_home(from)),
        None => format!("Moved to `{to}`"),
    }
}

fn refuse(app: &mut App, message: &str, outcome: &'static str) {
    let severity = match outcome {
        "refused_busy" => Severity::Warning,
        _ => Severity::Error,
    };
    crate::notices::inline(app, severity, message);
    crate::telemetry::session_relocation_done(app, "error", Some(outcome));
}
