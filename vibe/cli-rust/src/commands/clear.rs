//! `/clear`: reset the transcript, then adopt the session the server hands back.

use std::sync::Arc;
use std::time::Duration;

use serde_json::Value;

use super::event::{dispatch, CommandEvent};
use super::submission::new_message_id;
use crate::app::{App, QueuedPrompt};
use crate::resume_picker::short_id;
use crate::server::Client;
use crate::server::{method, PublicSessionState, SessionHistoryClearParams, TokenUsage};
use crate::session_exit::is_resumable;
use crate::transcript::local;

/// Bound on the pre-clear `session/log/read`; past it the notice omits the previous session.
const PREVIOUS_SESSION_READ_TIMEOUT: Duration = Duration::from_millis(500);

/// The prompt `/clear <prompt>` seeds the fresh session with. It skips prompt
/// preparation, so an `[Image #N]` placeholder goes as the `@path` mention the
/// server resolves itself.
pub fn seed(app: &App, value: &str) -> Option<QueuedPrompt> {
    let prompt = value.split_once(char::is_whitespace)?.1.trim();
    (!prompt.is_empty()).then(|| QueuedPrompt {
        message_id: new_message_id(),
        text: app.chat_input.pasted_images.with_paths(prompt),
    })
}

pub(super) fn clear_history(app: &mut App, client: &Arc<Client>, value: &str) {
    let seed = seed(app, value);
    app.view.transcript.clear();
    app.todo_tracker.clear();
    app.queue.clear();
    app.view.transcript_cache.invalidate_layouts();
    app.view.scroll = 0;
    app.view.scroll_target = 0;
    local::add_message(
        &mut app.view.transcript,
        &new_message_id(),
        "command",
        "clear",
    );
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        // Read before the clear: the server drops the old session once it is replaced.
        let previous_session_id = is_resumable(&client, &session_id, PREVIOUS_SESSION_READ_TIMEOUT)
            .await
            .then(|| session_id.clone());
        let params = SessionHistoryClearParams { session_id };
        let Ok(value) = serde_json::to_value(params) else {
            return;
        };
        let event = match client.request(method::SESSION_HISTORY_CLEAR, value).await {
            Ok(result) => cleared(result, previous_session_id, seed),
            Err(error) => CommandEvent::Error(format!("Failed to clear history: {error}")),
        };
        let _ = tx.try_send(event);
    });
}

/// Read a `session/history/clear` response: the server replaced the session, so
/// the client has to adopt the new id or every later prompt is rejected.
pub fn cleared(
    result: Value,
    previous_session_id: Option<String>,
    seed: Option<QueuedPrompt>,
) -> CommandEvent {
    match result
        .get("state")
        .cloned()
        .and_then(|state| serde_json::from_value::<PublicSessionState>(state).ok())
    {
        Some(state) => CommandEvent::Cleared {
            session_id: state.session.id,
            usage: state.session.token_usage,
            child_sessions: state.child_sessions,
            previous_session_id,
            seed,
        },
        None => CommandEvent::Error("Clearing the history returned no session state.".to_owned()),
    }
}

/// Adopt the session `session/history/clear` created, on the main thread.
pub fn apply_cleared(
    app: &mut App,
    session_id: String,
    usage: Option<TokenUsage>,
    child_sessions: Vec<crate::server::PublicChildSession>,
) {
    app.terminal_notifier.set_default_title("");
    app.set_session_id(session_id);
    app.session.active_turn_id = None;
    // The cleared replacement owns its own child sessions (Python `replace_state`).
    app.subagents.seed_snapshot(child_sessions);
    // Python `SessionContextCleared` -> `_reset_subagent_views`.
    crate::subagents::reset_views(app);
    crate::subagents::refresh(app);
    // Python `reset_usage_baseline` on the clear adopt, falling back to the
    // stats still held when the fresh state carries no usage.
    let baseline = usage.unwrap_or_else(|| crate::session_exit::current_usage(app));
    app.session.usage_baseline = Some(baseline);
}

/// Python `_clear_history`'s notice, naming the previous session when it can be resumed.
pub fn new_conversation_text(previous_session_id: Option<&str>) -> String {
    let Some(previous) = previous_session_id else {
        return "New conversation started.".to_owned();
    };
    let short = short_id(previous);
    format!(
        "New conversation started.\n\nPrevious session: `{short}`\n\
         To resume it later, run: `vibe --resume {short}`"
    )
}

/// Send the prompt that came with `/clear <prompt>`, once the new id is live.
pub fn start_seed_turn(app: &App, client: &Arc<Client>, session_id: String, seed: QueuedPrompt) {
    let client = client.clone();
    let telemetry = app.telemetry_tx.clone();
    tokio::spawn(async move {
        super::submission::start_turn(client, session_id, seed, telemetry).await;
    });
}
