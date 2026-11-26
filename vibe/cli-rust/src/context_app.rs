//! `/context` bottom app: the repartition of the context window, fetched from
//! the app server (`session/context/read`) and rendered by `ui::context_app`.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::json;

use crate::app::App;
use crate::commands::CommandEvent;
use crate::input::deliver;
use crate::server::request_error::describe;
use crate::server::{method, Client, ContextBreakdown, RequestFailure};

/// The open `/context` app.
#[derive(Default)]
pub struct State {
    pub open: bool,
    pub breakdown: Option<ContextBreakdown>,
    pub error: Option<String>,
    /// Bumped on every read so a stale answer cannot overwrite a newer one.
    pub generation: u64,
}

/// A `session/context/read` answer.
pub enum Event {
    Read(Result<ContextBreakdown, String>),
}

/// `/context` and the footer's token counter: open the app and read the breakdown.
pub fn open(app: &mut App, client: &Arc<Client>) {
    app.context_app.open = true;
    app.context_app.breakdown = None;
    app.context_app.error = None;
    read(app, client);
}

/// Re-read while the app is open (e.g. after `session/statsUpdated`).
pub fn refresh(app: &mut App, client: &Arc<Client>) {
    if app.context_app.open {
        read(app, client);
    }
}

fn read(app: &mut App, client: &Arc<Client>) {
    // The footer shows the viewed subagent's tokens, so read that session's breakdown.
    let session_id = app
        .subagents
        .viewed_subagent_id
        .clone()
        .or_else(|| app.session.session_id.clone());
    let Some(session_id) = session_id else {
        app.context_app.error = Some("No active session.".to_owned());
        return;
    };
    let generation = app.context_app.generation.wrapping_add(1);
    app.context_app.generation = generation;
    let tx = app.command_tx.clone();
    let client = client.clone();
    crate::work::spawn(async move {
        let event = Event::Read(
            client
                .request(
                    method::SESSION_CONTEXT_READ,
                    json!({"sessionId": session_id}),
                )
                .await
                .map_err(|error| {
                    if unsupported_rpc(&error) {
                        UNSUPPORTED.to_owned()
                    } else {
                        describe(&error)
                    }
                })
                .and_then(|value| {
                    serde_json::from_value(value.get("breakdown").cloned().unwrap_or(value))
                        .map_err(|error| error.to_string())
                }),
        );
        deliver(tx, CommandEvent::Context(Box::new((generation, event)))).await;
    });
}

/// Apply the server's answer; a stale generation is dropped.
pub fn apply_event(app: &mut App, generation: u64, event: Event) {
    if generation != app.context_app.generation {
        return;
    }
    match event {
        Event::Read(Ok(breakdown)) => {
            app.context_app.breakdown = Some(breakdown);
            app.context_app.error = None;
        }
        Event::Read(Err(error)) => {
            // A failed read must not leave the previous split painted.
            app.context_app.breakdown = None;
            if error == UNSUPPORTED {
                app.context_app.error =
                    Some("This app server does not report a context breakdown yet.".to_owned());
            } else {
                app.context_app.error = Some(format!("Failed to read context usage: {error}"));
            }
        }
    }
}

/// The legacy backend answers unknown methods with `not_implemented`.
const UNSUPPORTED: &str = "unsupported";

fn unsupported_rpc(error: &anyhow::Error) -> bool {
    matches!(
        error.downcast_ref::<RequestFailure>(),
        Some(RequestFailure::Rpc(rpc))
            if matches!(rpc.code().as_str(), "not_implemented" | "method_not_found" | "-32601")
    )
}

/// The app owns the keyboard while open; Esc/q close it.
pub fn handle_key(app: &mut App, key: KeyEvent) {
    if key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER)
    {
        return;
    }
    match key.code {
        KeyCode::Esc => close(app),
        KeyCode::Char('q') => close(app),
        _ => {}
    }
}

pub fn close(app: &mut App) {
    app.context_app.open = false;
}
