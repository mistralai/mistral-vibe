//! What the keys do to the highlighted session: open, new, rename, archive.

use std::sync::Arc;

use super::requests;
use crate::app::{App, Severity};
use crate::server::request_error::SESSION_BUSY_MESSAGE;
use crate::server::{AgentConfig, Client};
use crate::trust_gate::{Outcome, TrustNext};

const BUSY: &str = "Wait for the current turn to finish before switching sessions.";
const CANCELLED: &str = "Cancelled: the folder is not trusted.";

pub use super::answers::{apply_event, Event};

/// Confirmations fade; refusals and failures stay until a key answers them once read.
pub(super) fn notice(app: &mut App, severity: Severity, text: &str) {
    match severity {
        Severity::Information => crate::ui::notice::show(app, severity, text, 4),
        _ => crate::notices::inline(app, severity, text),
    }
}

fn record(app: &App, action: &'static str) {
    let page = &app.sessions_page;
    let same_project = super::selected(app).map(|session| session.cwd == app.session.cwd);
    let position = (!page.view.rows.is_empty()).then(|| page.browse.list.selected());
    crate::telemetry::session_search_selected(
        app,
        action,
        position,
        !page.browse.search.query.is_empty(),
        same_project,
    );
}

/// The agent config a session in `cwd` opens with: the `--add-dir` roots and
/// `--trust` belong to the launch folder, so they stay behind.
fn config_for(app: &App, cwd: Option<&str>) -> AgentConfig {
    let mut config = app.session.agent_config.clone();
    config.worktree = None;
    if let Some(cwd) = cwd.filter(|cwd| Some(*cwd) != app.session.cwd.as_deref()) {
        config.cwd = Some(cwd.to_owned());
        config.workspace_roots.clear();
        config.trust_workspace = false;
    }
    config
}

/// Enter / →: open the highlighted session, after the trust gate of its folder.
pub fn open(app: &mut App, client: &Arc<Client>) {
    let Some(session) = super::selected(app).cloned() else {
        return;
    };
    if app.session.session_id.as_deref() == Some(session.id.as_str()) {
        return super::close(app);
    }
    if app.sessions_page.locked.contains(&session.id) {
        return notice(app, Severity::Warning, SESSION_BUSY_MESSAGE);
    }
    if !super::idle(app) {
        return notice(app, Severity::Warning, BUSY);
    }
    record(app, "open");
    app.sessions_page.busy = Some(session.id.clone());
    let ticket = next_ticket(app);
    match session
        .cwd
        .filter(|cwd| Some(cwd) != app.session.cwd.as_ref())
    {
        Some(cwd) => crate::trust_gate::then(
            app,
            client,
            cwd,
            TrustNext::Open {
                session_id: session.id,
                ticket,
            },
        ),
        None => resume(app, client, session.id, ticket),
    }
}

/// `n`: a new session in the highlighted row's folder.
pub fn new(app: &mut App, client: &Arc<Client>) {
    if !super::idle(app) {
        return notice(app, Severity::Warning, BUSY);
    }
    record(app, "new");
    let cwd = super::selected(app)
        .and_then(|session| session.cwd.clone())
        .or_else(|| app.session.cwd.clone())
        .unwrap_or_default();
    app.sessions_page.busy = Some(String::new());
    let ticket = next_ticket(app);
    match Some(&cwd) != app.session.cwd.as_ref() {
        true => {
            let next = TrustNext::New {
                cwd: cwd.clone(),
                ticket,
            };
            crate::trust_gate::then(app, client, cwd, next)
        }
        false => {
            requests::start(app, client, config_for(app, None), ticket);
            super::switch::start(app, String::new());
        }
    }
}

fn next_ticket(app: &mut App) -> u64 {
    app.sessions_page.ticket += 1;
    app.sessions_page.ticket
}

/// Whether the open or new numbered `ticket` is still the one the page waits on:
/// opening or closing the page cancels it, and a later one replaces it.
pub fn current(app: &App, ticket: u64) -> bool {
    app.sessions_page.busy.is_some() && app.sessions_page.ticket == ticket
}

/// The trust gate settled for `Open`.
pub fn after_trust_open(
    app: &mut App,
    client: &Arc<Client>,
    id: String,
    ticket: u64,
    outcome: Outcome,
) {
    if !current(app, ticket) {
        return;
    }
    match outcome {
        Outcome::Trusted => resume(app, client, id, ticket),
        Outcome::Declined => settle_refusal(app, Severity::Information, CANCELLED),
        Outcome::Failed(message) => settle_refusal(app, Severity::Error, &message),
    }
}

/// The trust gate settled for `New`.
pub fn after_trust_new(
    app: &mut App,
    client: &Arc<Client>,
    cwd: String,
    ticket: u64,
    outcome: Outcome,
) {
    if !current(app, ticket) {
        return;
    }
    match outcome {
        Outcome::Trusted => {
            requests::start(app, client, config_for(app, Some(&cwd)), ticket);
            super::switch::start(app, String::new());
        }
        Outcome::Declined => settle_refusal(app, Severity::Information, CANCELLED),
        Outcome::Failed(message) => settle_refusal(app, Severity::Error, &message),
    }
}

pub(super) fn settle_refusal(app: &mut App, severity: Severity, text: &str) {
    app.sessions_page.busy = None;
    notice(app, severity, text);
}

fn resume(app: &mut App, client: &Arc<Client>, id: String, ticket: u64) {
    let cwd = app
        .sessions_page
        .sessions
        .iter()
        .find(|session| session.id == id)
        .and_then(|session| session.cwd.clone());
    let config = config_for(app, cwd.as_deref());
    requests::resume(app, client, id.clone(), config, ticket);
    super::switch::start(app, id);
}

/// `a`: archive, or restore in the archived view.
pub fn archive(app: &mut App, client: &Arc<Client>) {
    let Some(id) = super::selected(app).map(|session| session.id.clone()) else {
        return;
    };
    if app.session.session_id.as_deref() == Some(id.as_str()) {
        return notice(
            app,
            Severity::Warning,
            "The current session can't be archived.",
        );
    }
    if app.sessions_page.locked.contains(&id) {
        return notice(app, Severity::Warning, SESSION_BUSY_MESSAGE);
    }
    let archived = !app.sessions_page.browse.archived;
    record(app, if archived { "archive" } else { "restore" });
    requests::archive(app, client, id, archived);
}

/// Enter on the rename field: save a non-empty title.
pub fn rename(app: &mut App, client: &Arc<Client>) {
    let Some(rename) = app.sessions_page.rename.as_ref() else {
        return;
    };
    let title = rename.text.trim().to_owned();
    // An empty title keeps the modal open: there is nothing to save.
    if title.is_empty() {
        return;
    }
    let id = rename.id.clone();
    app.sessions_page.rename = None;
    record(app, "rename");
    requests::rename(app, client, id, title);
}
