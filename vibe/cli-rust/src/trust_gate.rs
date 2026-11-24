//! The workspace-trust gate mid-session: `then` checks a folder's trust, asks through
//! the startup gate UI when needed, persists the answer, then runs a `TrustNext`.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent};
use serde_json::json;

use crate::app::App;
use crate::server::{method, Client, WorkspaceTrustDetails, WorkspaceTrustStatusResponse};
use crate::trust_folders::{self, DECLINE};

/// What runs once the folder is trusted. Add a variant and its arm in `settle`.
pub enum TrustNext {
    /// `/cd` for the session `session_id` was attached when it ran.
    Cd {
        target: String,
        session_id: String,
        ticket: u64,
    },
    Open {
        session_id: String,
        ticket: u64,
    },
    New {
        cwd: String,
        ticket: u64,
    },
}

/// How the gate ended for a `TrustNext`.
pub enum Outcome {
    Trusted,
    /// "Don't trust", Esc or Ctrl+C: nothing was persisted.
    Declined,
    Failed(String),
}

impl TrustNext {
    /// Whether the action still waits for this gate: the page may have cancelled it.
    fn wanted(&self, app: &App) -> bool {
        match self {
            Self::Cd {
                session_id, ticket, ..
            } => crate::commands::cd::current(app, session_id, *ticket),
            Self::Open { ticket, .. } | Self::New { ticket, .. } => {
                crate::sessions_page::actions::current(app, *ticket)
            }
        }
    }

    fn settle(self, app: &mut App, client: &Arc<Client>, outcome: Outcome) {
        match self {
            Self::Cd {
                target,
                session_id,
                ticket,
            } => crate::commands::cd::after_trust(app, client, target, session_id, ticket, outcome),
            Self::Open { session_id, ticket } => crate::sessions_page::actions::after_trust_open(
                app, client, session_id, ticket, outcome,
            ),
            Self::New { cwd, ticket } => {
                crate::sessions_page::actions::after_trust_new(app, client, cwd, ticket, outcome)
            }
        }
    }
}

/// The request an open mid-session gate answers.
pub struct Pending {
    pub cwd: String,
    pub next: TrustNext,
}

/// A trust request's answer, applied on the main thread.
pub enum Event {
    Status(TrustNext, Result<Option<WorkspaceTrustDetails>, String>),
    Decided(TrustNext, Result<(), String>),
}

/// Run `next` once `cwd` is trusted: at once when it already is, else after the gate.
pub fn then(app: &mut App, client: &Arc<Client>, cwd: String, next: TrustNext) {
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let status = client
            .request_err(method::WORKSPACE_TRUST_STATUS, json!({ "cwd": cwd }))
            .await
            .map_err(|error| error.message)
            .and_then(|value| {
                serde_json::from_value::<WorkspaceTrustStatusResponse>(value)
                    .map(|status| status.details)
                    .map_err(|error| error.to_string())
            });
        Event::Status(next, status)
    });
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    match event {
        // A cancelled action asks for no trust decision.
        Event::Status(next, _) if !next.wanted(app) => {}
        Event::Status(next, Ok(None)) => next.settle(app, client, Outcome::Trusted),
        Event::Status(next, Ok(Some(details))) => {
            let cwd = details.cwd.clone();
            trust_folders::open(app, details);
            app.trust.pending = Some(Pending { cwd, next });
        }
        Event::Status(next, Err(error)) | Event::Decided(next, Err(error)) => {
            next.settle(app, client, Outcome::Failed(error))
        }
        Event::Decided(next, Ok(())) => next.settle(app, client, Outcome::Trusted),
    }
}

/// Keys on the open gate; `true` quits the client, which only the startup gate does.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) -> bool {
    if app.trust.pending.is_none() {
        return trust_folders::handle_key(app, key);
    }
    if key.code == KeyCode::Esc || trust_folders::is_quit(key) {
        trust_folders::close(app);
        answer(app, client, DECLINE);
        return false;
    }
    trust_folders::handle_key(app, key);
    if let Some(decision) = app.trust.answer.take() {
        answer(app, client, decision);
    }
    false
}

/// Persist a trust decision, then settle; a decline persists nothing.
fn answer(app: &mut App, client: &Arc<Client>, decision: &'static str) {
    let Some(Pending { cwd, next }) = app.trust.pending.take() else {
        return;
    };
    if decision == DECLINE {
        return next.settle(app, client, Outcome::Declined);
    }
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let params = json!({ "decision": decision, "cwd": cwd, "sessionId": null });
        let result = client
            .request_err(method::WORKSPACE_TRUST_DECISION, params)
            .await
            .map(|_| ())
            .map_err(|error| error.message);
        Event::Decided(next, result)
    });
}
