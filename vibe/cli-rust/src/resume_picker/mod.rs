//! Fast saved-session picker and in-place resume.

mod notice;
mod request;

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;

use crate::app::{App, Status};
use crate::server::{Client, PublicSessionState};
use crate::session_exit;

pub use notice::rebase;
use request::Call;

#[derive(Clone, Default)]
pub struct Session {
    pub id: String,
    pub title: String,
    pub preview: String,
    pub updated_at: u64,
    pub cwd: Option<String>,
}

pub enum Event {
    Loaded(Vec<Session>),
    Preview {
        request: u64,
        state: PublicSessionState,
    },
    Resumed {
        id: String,
        state: PublicSessionState,
    },
    Deleted {
        id: String,
        error: Option<String>,
    },
    Error(String),
}

pub fn open(app: &mut App, client: &Arc<Client>) {
    begin(app);
    let cwd = app.session.cwd.clone();
    if let Some(call) = call(app, client) {
        request::list(call, cwd);
    }
}

/// The handles a spawned call needs; `None` once the picker channel is gone.
fn call(app: &mut App, client: &Arc<Client>) -> Option<Call> {
    let tx = app.resume_picker.tx.clone()?;
    Some(Call {
        client: client.clone(),
        tx,
        pending: app.commit_started(),
    })
}

fn begin(app: &mut App) {
    app.resume_picker.open = true;
    app.resume_picker.sessions.clear();
    app.resume_picker.selected = 0;
    app.resume_picker.scroll = 0;
    app.resume_picker.free_scroll = false;
    app.resume_picker.preview_request = 0;
    app.resume_picker.previewing = false;
    app.resume_picker.resuming = false;
    app.resume_picker.delete_confirm = None;
    app.resume_picker.deleting = None;
    app.resume_picker.transcript = Some(app.view.transcript.snapshot());
}

/// Open `--resume`'s picker on the list the handshake read before
/// `session/start`, so it is on screen while the engine is still coming up.
pub fn open_listed(app: &mut App, client: &Arc<Client>, value: &Value) {
    if !matches!(
        app.session.startup_resume,
        crate::cli::StartupResume::Picker
    ) {
        return;
    }
    app.session.startup_resume = crate::cli::StartupResume::None;
    begin(app);
    apply_event(app, client, Event::Loaded(request::sessions(value)));
}

pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    // A delete in flight owns the picker, as Python's pending-delete state does.
    if app.resume_picker.deleting.is_some() {
        return;
    }
    let chorded = key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
    match key.code {
        KeyCode::Esc if app.resume_picker.delete_confirm.take().is_some() => {}
        KeyCode::Esc => {
            notice::close(app);
            notice::result(app, "Resume cancelled.");
            drop_initial_prompt(app);
        }
        KeyCode::Up => navigate(app, client, false),
        KeyCode::Down => navigate(app, client, true),
        KeyCode::Char('k') if !chorded => navigate(app, client, false),
        KeyCode::Char('j') if !chorded => navigate(app, client, true),
        KeyCode::Enter if awaiting_confirm(app) => {}
        KeyCode::Enter => resume(app, client),
        KeyCode::Char('d') if !chorded => delete(app, client),
        _ => {}
    }
}

/// Python ignores Enter on a row waiting for its second `d`. The current
/// session's "Can't delete current session" is feedback, not a confirmation, so
/// Enter still resumes there.
fn awaiting_confirm(app: &App) -> bool {
    let Some(id) = app.resume_picker.delete_confirm.as_deref() else {
        return false;
    };
    app.session.session_id.as_deref() != Some(id)
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    match event {
        Event::Loaded(sessions) => {
            app.resume_picker.cwd = sessions.iter().find_map(|session| session.cwd.clone());
            app.resume_picker.sessions = sessions;
            if app.resume_picker.sessions.is_empty() {
                notice::close(app);
                notice::result(app, "No sessions found for this directory.");
                return;
            }
            if let Some(current) = app.session.session_id.as_deref() {
                app.resume_picker.selected = app
                    .resume_picker
                    .sessions
                    .iter()
                    .position(|session| session.id == current)
                    .unwrap_or(0);
            }
            preview(app, client);
        }
        Event::Preview { request, state }
            if app.resume_picker.open && request == app.resume_picker.preview_request =>
        {
            app.view.transcript.load_snapshot(&state);
            app.expand_rebuilt_tools();
        }
        Event::Resumed { id, state } => {
            if !notice::finish_resume(app) {
                return;
            }
            app.resume_picker.open = false;
            app.resume_picker.transcript = None;
            app.session.session_id = Some(state.session.id.clone());
            app.terminal_notifier
                .set_default_title(state.session.title.as_deref().unwrap_or(""));
            app.session.resumed = true;
            app.session.active_turn_id = None;
            // Python `reset_usage_baseline` at resume adopt: the exit delta
            // restarts from the resumed session's accumulated usage.
            app.session.usage_baseline = Some(
                state
                    .session
                    .token_usage
                    .unwrap_or_else(|| session_exit::current_usage(app)),
            );
            // Python `_reset_presentation_after_resume`: retire the previous
            // session's open turn data and drop any summary still in flight.
            crate::turn_summary::on_turn_end(app, client);
            crate::turn_summary::cancel(app);
            app.queue.clear();
            // Python remounts every history widget fresh on resume, so manual
            // expansion state resets to the fold flag.
            app.view.expanded.clear();
            app.view.transcript.load_snapshot(&state);
            app.expand_rebuilt_tools();
            // Python rebuilds the transcript on resume and re-decides the
            // custom-tools deprecation against the resumed session.
            crate::startup::banners::rebuild_custom_tools_deprecation(app);
            app.set_status(Status::Ready);
            notice::result(app, &format!("Resumed session `{}`", short_id(&id)));
            crate::message_queue::flush_pending(app, client);
            crate::event_handler::process_initial_prompt(app, client);
        }
        Event::Deleted { id, error } => {
            app.resume_picker.deleting = None;
            if let Some(error) = error {
                notice::behind(app, |app| {
                    notice::error(app, &format!("Failed to delete session: {error}"))
                });
                return;
            }
            app.resume_picker
                .sessions
                .retain(|session| session.id != id);
            app.resume_picker.selected = app
                .resume_picker
                .selected
                .min(app.resume_picker.sessions.len().saturating_sub(1));
            notice::result(app, &format!("Deleted session `{}`.", short_id(&id)));
            if app.resume_picker.sessions.is_empty() {
                notice::close(app);
                notice::result(app, "No saved sessions left for this directory.");
                drop_initial_prompt(app);
                return;
            }
            preview(app, client);
        }
        // A late answer to a cancelled resume: the picker is already closed and
        // nobody is waiting on it.
        Event::Error(_) if !app.resume_picker.open => {
            notice::finish_resume(app);
        }
        // Restore the transcript the preview replaced; leaving `open` false on
        // its own would strand the preview on screen.
        Event::Error(message) => {
            notice::close(app);
            notice::error(app, &message);
            drop_initial_prompt(app);
        }
        Event::Preview { .. } => {}
    }
}

/// Python `_exit_picker_to_input`: a startup prompt never fires once the user
/// leaves the picker without resuming.
fn drop_initial_prompt(app: &mut App) {
    app.session.initial_prompt = None;
}

fn navigate(app: &mut App, client: &Arc<Client>, down: bool) {
    let len = app.resume_picker.sessions.len();
    if len == 0 {
        return;
    }
    app.resume_picker.selected = if down {
        (app.resume_picker.selected + 1) % len
    } else {
        (app.resume_picker.selected + len - 1) % len
    };
    app.resume_picker.free_scroll = false;
    app.resume_picker.delete_confirm = None;
    preview(app, client);
}

/// Scroll the saved-session viewport without changing the preview selection.
pub fn wheel(app: &mut App, down: bool) {
    if app.resume_picker.sessions.is_empty() {
        return;
    }
    app.resume_picker.free_scroll = true;
    app.resume_picker.scroll = if down {
        app.resume_picker.scroll.saturating_add(2)
    } else {
        app.resume_picker.scroll.saturating_sub(2)
    };
}

fn preview(app: &mut App, client: &Arc<Client>) {
    let Some(session) = app.resume_picker.sessions.get(app.resume_picker.selected) else {
        return;
    };
    let is_current = app.session.session_id.as_deref() == Some(session.id.as_str());
    if is_current && !app.resume_picker.previewing {
        return;
    }
    let id = session.id.clone();
    app.resume_picker.previewing = true;
    app.resume_picker.preview_request += 1;
    let request = app.resume_picker.preview_request;
    if let Some(tx) = app.resume_picker.tx.clone() {
        // Hold the app busy until the preview answer lands, so the replay idle
        // marker waits for it; `resume` events balance this in the event loop.
        app.commit_started();
        request::preview(client.clone(), tx, id, request);
    }
}

fn resume(app: &mut App, client: &Arc<Client>) {
    let Some(session) = app.resume_picker.sessions.get(app.resume_picker.selected) else {
        return;
    };
    if app.resume_picker.resuming {
        return;
    }
    let id = session.id.clone();
    let Some(call) = call(app, client) else {
        return;
    };
    // Busy without a spinner (Python keeps the loading widget unmounted): the
    // status stays as it is, so Esc can still cancel and notifications flow.
    app.resume_picker.resuming = true;
    app.commit_started();
    // Python resends `_session_options` on a picker resume too.
    let params = crate::startup::resume_params(&id, &app.session.agent_config);
    request::resume(call, id, params);
}

fn delete(app: &mut App, client: &Arc<Client>) {
    let Some(session) = app.resume_picker.sessions.get(app.resume_picker.selected) else {
        return;
    };
    if app.session.session_id.as_deref() == Some(&session.id) {
        app.resume_picker.delete_confirm = Some(session.id.clone());
        return;
    }
    let id = session.id.clone();
    if app.resume_picker.delete_confirm.as_deref() != Some(&id) {
        app.resume_picker.delete_confirm = Some(id);
        return;
    }
    app.resume_picker.delete_confirm = None;
    let Some(call) = call(app, client) else {
        return;
    };
    app.resume_picker.deleting = Some(id.clone());
    request::delete(call, id);
}

/// Python `shorten_session_id`: the first 8 characters of a session id.
pub(crate) fn short_id(id: &str) -> &str {
    id.get(..8).unwrap_or(id)
}
