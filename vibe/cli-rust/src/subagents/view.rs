//! Child-view lifecycle: scroll memory, fetch scheduling, reset (Python `app.py`).

use crate::app::App;
use crate::subagents::list::update_sessions;

/// The transcript and render cache hit tests and clipboard extraction must
/// resolve: the viewed child's while its view is open, else the main pair.
/// The render loop only swaps the child in during drawing (ui/mod.rs), so
/// event-time consumers follow the viewed child through here.
pub fn active_transcript(
    app: &App,
) -> (
    &crate::transcript::Transcript,
    &crate::utils::transcript_cache::TranscriptCache,
) {
    match app
        .subagents
        .viewed_subagent_id
        .as_deref()
        .and_then(|child_id| app.subagents.transcripts.child(child_id))
    {
        Some(child) => (&child.transcript, &child.cache),
        None => (&app.view.transcript, &app.view.transcript_cache),
    }
}

/// Python `_refresh_context_progress`: while a child is viewed the context
/// progress shows the child's own context usage; on main it shows the session's.
pub fn refresh_context_progress(app: &mut App) {
    match app.subagents.viewed_child() {
        Some(child) => app.session.tokens.0 = child.context_tokens(),
        // Only the current count is parked; the max stays live, because
        // `/model` updates `app.session.tokens.1` alone.
        None => app.session.tokens.0 = app.subagents.main_tokens.0,
    }
}

/// Python `_remember_transcript_scroll`: park the current offset under the
/// session being left (`None` is the main conversation).
pub(super) fn remember_scroll(app: &mut App) {
    let key = app.subagents.viewed_subagent_id.clone();
    app.subagents
        .transcripts
        .scroll_offsets
        .insert(key, app.view.scroll);
}

/// Python `_restore_transcript_scroll`: resume the session's stored offset, or
/// anchor at the newest content when it has none.
pub(super) fn restore_scroll(app: &mut App, key: Option<&str>) {
    let stored = app
        .subagents
        .transcripts
        .scroll_offsets
        .get(&key.map(str::to_owned))
        .copied()
        .unwrap_or(0);
    app.view.scroll = stored;
    app.view.scroll_target = stored;
}

/// Anchor the viewed transcript to the newest content (Python `chat_widget.anchor`).
pub fn anchor(app: &mut App) {
    app.view.scroll = 0;
    app.view.scroll_target = 0;
}

/// Python `_remember_subagent_instruction`: synthesize the parent's spawn
/// task as a `parent-instruction:<child>` turn-start user message.
pub fn remember_parent_instruction_from_entry(app: &mut App, entry: &serde_json::Value) {
    let Some(detail) = entry.get("detail") else {
        return;
    };
    let is_spawn = detail.get("kind").and_then(serde_json::Value::as_str) == Some("subagent")
        && detail.get("toolName").and_then(serde_json::Value::as_str) == Some("subagent.spawn");
    if !is_spawn {
        return;
    }
    let (Some(child_session_id), Some(task)) = (
        detail
            .get("childSessionId")
            .and_then(serde_json::Value::as_str),
        detail
            .pointer("/input/task")
            .and_then(serde_json::Value::as_str),
    ) else {
        return;
    };
    let instruction = serde_json::json!({
        "id": format!("parent-instruction:{child_session_id}"),
        "sessionId": child_session_id,
        "createdAt": entry.get("createdAt").cloned().unwrap_or(serde_json::Value::Null),
        "updatedAt": entry.get("updatedAt").cloned().unwrap_or(serde_json::Value::Null),
        "generationStatus": "completed",
        "type": "message",
        "role": "user",
        "source": "turn_start",
        "content": [{"type": "text", "text": task}],
    });
    app.subagents.transcripts.remember_instruction(instruction);
}

/// Python `_schedule_subagent_transcript_refresh`: mark the drain dirty and
/// start the 50ms sleep when one is not already running.
pub fn schedule_refresh(app: &mut App) {
    app.subagents.refresh_requested = true;
    arm_refresh(app);
}

/// Arm one 50ms sleep. A deadline already set stays put, so a streaming child
/// still refreshes instead of waiting for 50ms of quiet.
fn arm_refresh(app: &mut App) {
    if app.subagents.refresh_at.is_some() || app.subagents.refresh_in_flight {
        return;
    }
    if !app.subagents.refresh_requested || app.subagents.viewed_subagent_id.is_none() {
        app.subagents.refresh_requested = false;
        return;
    }
    // Python clears the dirty bit, sleeps 50ms, then fetches.
    app.subagents.refresh_requested = false;
    app.subagents.refresh_at = Some(std::time::Instant::now() + super::REFRESH_DEBOUNCE);
}

/// The debounce elapsed. Fetch once; requests from the wait stay dirty and
/// run after this read lands.
pub fn start_refresh(app: &mut App, client: &std::sync::Arc<crate::server::Client>) {
    app.subagents.refresh_at = None;
    if !spawn_refresh(app, client) {
        arm_refresh(app);
    }
}

/// One history read at a time (Python awaits the refresh), so a slower older
/// `session/read` cannot finish later and replace a newer transcript.
fn spawn_refresh(app: &mut App, client: &std::sync::Arc<crate::server::Client>) -> bool {
    if app.subagents.refresh_in_flight {
        return false;
    }
    let Some(session_id) = app.subagents.viewed_subagent_id.clone() else {
        return false;
    };
    // No answer channel: skipping keeps the drain from waiting on a lost event.
    let Some(tx) = app.subagents.tx.clone() else {
        return false;
    };
    let (known_ids, cached_complete) = match app.subagents.transcripts.child(&session_id) {
        Some(transcript) => (
            transcript
                .history
                .as_deref()
                .unwrap_or(&[])
                .iter()
                .filter_map(|entry| entry.get("id").and_then(serde_json::Value::as_str))
                .map(str::to_owned)
                .collect(),
            transcript.history_complete,
        ),
        None => (std::collections::HashSet::new(), false),
    };
    app.subagents.refresh_in_flight = true;
    // Count the fetch as in-flight work so the idle marker waits for it.
    let pending = app.commit_started();
    super::fetch::fetch_history(
        client.clone(),
        session_id,
        Some(tx),
        known_ids,
        cached_complete,
        pending,
    );
    true
}

/// Apply a fetch answer on the main thread (Python `_refresh_subagent_transcript`).
pub fn apply_event(app: &mut App, event: super::Event) {
    // This read has landed. A request that arrived during it arms the next window.
    app.subagents.refresh_in_flight = false;
    match event {
        super::Event::Fetched {
            session_id,
            history,
            complete,
        } => {
            if app.subagents.viewed_subagent_id.as_deref() == Some(session_id.as_str()) {
                // The viewport keeps its offset across the content change; an
                // anchored view stays anchored (Python anchors or restores).
                let show_thinking = crate::startup::read_show_thinking_nodes(&app.session.runtime);
                let changed = app.subagents.transcripts.replace_history(
                    &session_id,
                    history,
                    complete,
                    show_thinking,
                );
                if changed {
                    expand_rebuilt_tools(app, &session_id);
                }
            }
        }
        super::Event::Failed { session_id } => {
            if app.subagents.viewed_subagent_id.as_deref() == Some(session_id.as_str()) {
                app.subagents.transcripts.show_error(&session_id);
            }
        }
    }
    arm_refresh(app);
}

/// Python `build_history_widgets(tools_collapsed=...)`: with the bulk fold
/// open, a rebuilt child expands its groups and reasoning rows.
fn expand_rebuilt_tools(app: &mut App, session_id: &str) {
    if app.view.tools_collapsed {
        return;
    }
    let ids = match app.subagents.transcripts.child_mut(session_id) {
        Some(child) => child.transcript.expandable_ids(),
        None => Vec::new(),
    };
    app.view.expanded.extend(ids);
}

/// Python `_reset_subagent_views`: drop every child view on a session reset.
/// Every Python reset flow re-anchors the chat afterwards (`scroll_home` on
/// clear and context-cleared, `anchor` on the compact rebuild), so the child's
/// transcript scroll must not leak into the rebuilt main conversation.
pub fn reset_views(app: &mut App) {
    app.subagents.refresh_at = None;
    app.subagents.refresh_requested = false;
    // Leave `refresh_in_flight` set so the outstanding answer, not a new read, releases the drain.
    app.subagents.viewed_subagent_id = None;
    app.subagents.transcripts.clear();
    anchor(app);
    refresh_context_progress(app);
    update_sessions(app);
}

/// Python `_append_subagent_read_only_message`: a local row scolding the
/// Ctrl+C, then anchor the viewed transcript.
pub fn append_read_only_message(app: &mut App, session_id: &str) {
    app.subagents.transcripts.append_local_user_message(
        session_id,
        super::READ_ONLY_MESSAGE,
        super::Severity::Error,
    );
    if app.subagents.viewed_subagent_id.as_deref() == Some(session_id) {
        anchor(app);
    }
}
