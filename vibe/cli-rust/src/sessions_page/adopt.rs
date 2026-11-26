//! Taking over a session the page opened or started: transcript, turn, queue and folder.

use std::sync::Arc;

use crate::app::App;
use crate::commands::submission;
use crate::server::{Client, PublicSessionState};
use crate::session_exit;
use crate::transcript::local;

/// Adopt `state` as the attached session, replacing the one the page was opened from;
/// `verb` names what happened ("Resumed", "Started").
pub fn adopt(app: &mut App, client: &Arc<Client>, state: &PublicSessionState, verb: &str) {
    // The session left behind was on screen until now: nothing in it is unread.
    super::mark_current_seen(app, client);
    // The rows this client added to the session it leaves (its notices, its
    // own "Resumed" row) go with it; the adopted session mounts its own.
    app.view.transcript.clear();
    // The startup warnings belong to the session the page was opened on.
    crate::notices::close_startup(app);
    app.set_session_id(state.session.id.clone());
    app.terminal_notifier
        .set_default_title(state.session.title.as_deref().unwrap_or(""));
    app.session.active_turn_id = None;
    // The exit delta restarts from the adopted session's accumulated usage.
    app.session.usage_baseline = Some(
        state
            .session
            .token_usage
            .unwrap_or_else(|| session_exit::current_usage(app)),
    );
    // Retire the previous session's open turn data and drop any summary still in flight.
    crate::turn_summary::on_turn_end(app, client);
    crate::turn_summary::cancel(app);
    crate::message_queue::adopt(app, state.turn_queue.as_ref());
    app.subagents.seed_snapshot(state.child_sessions.clone());
    crate::subagents::reset_views(app);
    crate::subagents::refresh(app);
    // Every history row is rebuilt fresh, so manual expansion resets to the fold flag.
    app.view.expanded.clear();
    app.todo_tracker.seed_from_history(state.history.as_ref());
    crate::goal::adopt(app, state.session.goal.clone(), true);
    crate::startup::banners::dismiss_whats_new(app);
    load_history(app, state);
    if let Some(cwd) = state.session.cwd.clone() {
        crate::cwd::follow(app, cwd);
    }
    // `/cd -` goes back to the folder this session left, never the one the previous session was in.
    app.session.previous_cwd = None;
    crate::worktree::adopt_state(app, state);
    crate::background_work::adopt_state(app, state);
    crate::loops::adopt_state(app, state);
    crate::event_handler::adopt_active_turn(app, state);
    let text = format!("{verb} session `{}`", short_id(&state.session.id));
    result(app, &text);
    crate::message_queue::flush_pending(app, client);
    crate::event_handler::process_initial_prompt(app, client);
}

/// A status row under the adopted transcript.
pub fn result(app: &mut App, text: &str) {
    let id = submission::new_message_id();
    local::add_command_status(&mut app.view.transcript, &id, text);
}

fn scroll_to_bottom(app: &mut App) {
    app.view.scroll = 0;
    app.view.scroll_target = 0;
    app.view.last_total = 0;
    app.view.scroll_to_entry = None;
    crate::selection::cancel_drag(app);
    app.selection.region = None;
}

/// Load `state`'s history, keeping this client's own rows around it.
pub fn load_history(app: &mut App, state: &PublicSessionState) {
    scroll_to_bottom(app);
    app.view.transcript.load_snapshot(state);
    app.view
        .transcript_cache
        .start_history(app.view.transcript.entry_count());
    app.expand_rebuilt_tools();
}

/// The last 8 characters of a session id: random in a UUIDv7, unlike its time-ordered start.
pub fn short_id(id: &str) -> &str {
    id.get(id.len().saturating_sub(8)..).unwrap_or(id)
}
