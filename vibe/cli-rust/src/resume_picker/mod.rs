//! Fast saved-session picker and in-place resume.

mod notice;
mod request;

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;

use crate::app::{App, Status, ToastSeverity};
use crate::event_handler::SERVER_TOAST_SECS;
use crate::option_list::Click;
use crate::server::request_error::SESSION_BUSY_MESSAGE;
use crate::server::{Client, PublicSessionState};
use crate::session_exit;

pub(crate) use notice::load_history;
pub use notice::rebase;
use request::Call;
pub use request::{held, lease_locked};

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
    /// The lease snapshot's sessions held by another process.
    Leases(std::collections::HashSet<String>),
    Preview {
        request: u64,
        state: PublicSessionState,
    },
    PreviewFailed {
        request: u64,
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
    subscribe(app, client);
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
    app.resume_picker.preview_request += 1;
    app.resume_picker.open = true;
    app.resume_picker.sessions.clear();
    app.resume_picker.list.reset();
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
    app.session.resume_picker_shown = true;
    begin(app);
    subscribe(app, client);
    apply_event(app, client, Event::Loaded(request::sessions(value)));
}

/// Watch the session leases once per process, so the picker's locks are warm
/// before it opens, like the desktop's catalogue subscription.
pub fn subscribe(app: &mut App, client: &Arc<Client>) {
    if app.resume_picker.subscribed {
        return;
    }
    app.resume_picker.subscribed = true;
    if let Some(call) = call(app, client) {
        request::leases(call, app.session.session_id.clone());
    }
}

/// One `session/lease/updated` notification: the lock set follows while the
/// picker is open and stays warm between opens.
pub fn on_lease_updated(app: &mut App, client: &Client, params: &Value) {
    let Some(id) = params.get("sessionId").and_then(Value::as_str) else {
        return;
    };
    let is_current = app.session.session_id.as_deref() == Some(id);
    let id = id.to_owned();
    let locked = request::lease_locked(params.get("leaseHolder"), client.server_pid(), is_current);
    // Recorded before the subscribe answer, which can arrive later on its own channel.
    app.resume_picker.lease_notified.insert(id.clone());
    if locked {
        app.resume_picker.locked.insert(id);
    } else {
        app.resume_picker.locked.remove(&id);
    }
}

/// The lease watch died; stale locks are worse than none.
pub fn on_lease_ended(app: &mut App) {
    app.resume_picker.locked.clear();
    app.resume_picker.lease_ended = true;
}

/// A subscribe snapshot under newer lease notifications; ignored after the watch ends.
fn apply_lease_snapshot(app: &mut App, mut snapshot: std::collections::HashSet<String>) {
    if app.resume_picker.lease_ended {
        return;
    }
    let picker = &mut app.resume_picker;
    snapshot.retain(|id| !picker.lease_notified.contains(id));
    snapshot.extend(
        picker
            .lease_notified
            .iter()
            .filter(|id| picker.locked.contains(*id))
            .cloned(),
    );
    picker.locked = snapshot;
}

pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    // A delete in flight owns the picker.
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
            notice::cancelled(app, "Resume cancelled.");
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

/// A click on a session previews it, like a move; a click on the highlight resumes it.
pub fn click(app: &mut App, client: &Arc<Client>, click: Click) {
    if app.resume_picker.deleting.is_some() {
        return;
    }
    app.resume_picker.delete_confirm = None;
    match click {
        Click::Highlight(_) => preview(app, client),
        Click::Confirm(_) => resume(app, client),
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
                app.resume_picker.list.highlight(
                    app.resume_picker
                        .sessions
                        .iter()
                        .position(|session| session.id == current)
                        .unwrap_or(0),
                );
            }
            preview(app, client);
        }
        Event::Preview { request, state }
            if app.resume_picker.open && request == app.resume_picker.preview_request =>
        {
            notice::show_preview(app, &state);
        }
        Event::PreviewFailed { request }
            if app.resume_picker.open && request == app.resume_picker.preview_request =>
        {
            notice::preview_failed(app);
        }
        Event::Resumed { id, state } => {
            if !notice::finish_resume(app) {
                return;
            }
            app.resume_picker.open = false;
            app.resume_picker.transcript = None;
            // The attempt notices belong to the session the picker was opened
            // on; the resumed session's transcript must not inherit them.
            notice::clear_attempts(app);
            app.set_session_id(state.session.id.clone());
            app.terminal_notifier
                .set_default_title(state.session.title.as_deref().unwrap_or(""));
            app.session.resumed = true;
            app.session.active_turn_id = None;
            // Reset the usage baseline at resume adopt: the exit delta
            // restarts from the resumed session's accumulated usage.
            app.session.usage_baseline = Some(
                state
                    .session
                    .token_usage
                    .unwrap_or_else(|| session_exit::current_usage(app)),
            );
            // Retire the previous session's open turn data and drop any
            // summary still in flight.
            crate::turn_summary::on_turn_end(app, client);
            crate::turn_summary::cancel(app);
            crate::message_queue::adopt(app, state.turn_queue.as_ref());
            // Reset the subagent views; the resumed state owns its own children.
            crate::subagents::reset_views(app);
            app.subagents.seed_snapshot(state.child_sessions.clone());
            crate::subagents::refresh(app);
            // Every history row is rebuilt fresh on resume, so manual
            // expansion state resets to the fold flag.
            app.view.expanded.clear();
            app.todo_tracker.seed_from_history(state.history.as_ref());
            load_history(app, &state);
            crate::worktree::track_state(app, &state);
            // The transcript is rebuilt on resume, so re-decide the
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
                // A delete failure is feedback about the picker's row, not
                // transcript history: toast it instead of mounting a notice.
                app.show_toast(
                    format!("Failed to delete session: {error}"),
                    ToastSeverity::Error,
                    SERVER_TOAST_SECS,
                );
                return;
            }
            app.resume_picker
                .sessions
                .retain(|session| session.id != id);
            app.resume_picker.list.highlight(
                app.resume_picker
                    .list
                    .selected()
                    .min(app.resume_picker.sessions.len().saturating_sub(1)),
            );
            let deleted = format!("Deleted session `{}`.", short_id(&id));
            if app.resume_picker.sessions.is_empty() {
                // Closing drops notices mounted over the preview, so confirm after it.
                notice::close(app);
                notice::result(app, &deleted);
                notice::result(app, "No saved sessions left for this directory.");
                drop_initial_prompt(app);
                return;
            }
            notice::result(app, &deleted);
            preview(app, client);
        }
        Event::Leases(snapshot) => apply_lease_snapshot(app, snapshot),
        // A late answer to a cancelled resume: the picker is already closed and
        // nobody is waiting on it.
        Event::Error(_) if !app.resume_picker.open => {
            notice::finish_resume(app);
        }
        // Restore the transcript the preview replaced; leaving `open` false on
        // its own would strand the preview on screen.
        Event::Error(message) => {
            notice::close(app);
            notice::error(app, notice::ATTEMPT_ERROR_ID, &message);
            drop_initial_prompt(app);
        }
        Event::Preview { .. } | Event::PreviewFailed { .. } => {}
    }
}

/// A startup prompt never fires once the user
/// leaves the picker without resuming.
fn drop_initial_prompt(app: &mut App) {
    app.session.initial_prompt = None;
    app.session.teleport_on_start = false;
}

fn navigate(app: &mut App, client: &Arc<Client>, down: bool) {
    let len = app.resume_picker.sessions.len();
    if len == 0 {
        return;
    }
    app.resume_picker.list.step(down, len, true, |_| true);
    app.resume_picker.delete_confirm = None;
    preview(app, client);
}

/// Scroll the saved-session viewport without changing the preview selection.
pub fn wheel(app: &mut App, down: bool) {
    if app.resume_picker.sessions.is_empty() {
        return;
    }
    app.resume_picker.list.wheel(down, 2);
}

fn preview(app: &mut App, client: &Arc<Client>) {
    notice::scroll_to_bottom(app);
    let Some(session) = app
        .resume_picker
        .sessions
        .get(app.resume_picker.list.selected())
    else {
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
    let Some(session) = app
        .resume_picker
        .sessions
        .get(app.resume_picker.list.selected())
    else {
        return;
    };
    if app.resume_picker.resuming {
        return;
    }
    // The lease already names another process; resume would only bounce the
    // conflict back. Toast the same refusal the delete path does, locally.
    if app.resume_picker.locked.contains(&session.id) {
        app.show_toast(
            SESSION_BUSY_MESSAGE.to_owned(),
            ToastSeverity::Error,
            SERVER_TOAST_SECS,
        );
        return;
    }
    let id = session.id.clone();
    let Some(call) = call(app, client) else {
        return;
    };
    // Busy without a spinner: the
    // status stays as it is, so Esc can still cancel and notifications flow.
    app.resume_picker.resuming = true;
    app.commit_started();
    // A picker resume resends the session's agent config too.
    let params = crate::startup::resume_params(&id, &app.session.agent_config);
    request::resume(call, id, params);
}

fn delete(app: &mut App, client: &Arc<Client>) {
    let Some(session) = app
        .resume_picker
        .sessions
        .get(app.resume_picker.list.selected())
    else {
        return;
    };
    if app.session.session_id.as_deref() == Some(&session.id) {
        app.resume_picker.delete_confirm = Some(session.id.clone());
        return;
    }
    // The lease already names another process, so the delete would only
    // bounce the conflict back; toast the same refusal locally.
    if app.resume_picker.locked.contains(&session.id) {
        app.show_toast(
            SESSION_BUSY_MESSAGE.to_owned(),
            ToastSeverity::Error,
            SERVER_TOAST_SECS,
        );
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

/// The first 8 characters of a session id.
pub(crate) fn short_id(id: &str) -> &str {
    id.get(..8).unwrap_or(id)
}
