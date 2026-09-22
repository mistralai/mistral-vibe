//! `/clear`: reset the transcript, then adopt the session the server hands back.

use std::sync::Arc;

use serde_json::Value;

use super::event::{dispatch, CommandEvent};
use super::simple::add_text;
use super::submission::new_message_id;
use crate::app::{App, QueuedPrompt};
use crate::server::Client;
use crate::server::{method, PublicSessionState, SessionHistoryClearParams, TokenUsage};
use crate::transcript::local;

pub(super) fn clear_history(app: &mut App, client: &Arc<Client>, value: &str) {
    let prompt = value
        .split_once(char::is_whitespace)
        .map(|(_, prompt)| prompt.trim().to_owned())
        .filter(|prompt| !prompt.is_empty());
    app.view.transcript.clear();
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
    add_text(app, "New conversation started.");
    let seed = prompt.map(|text| QueuedPrompt {
        message_id: new_message_id(),
        text,
    });
    if let Some(seed) = &seed {
        local::add_message(
            &mut app.view.transcript,
            &seed.message_id,
            "user",
            &seed.text,
        );
    }
    let Some((session_id, tx)) = dispatch(app) else {
        return;
    };
    let client = client.clone();
    tokio::spawn(async move {
        let params = SessionHistoryClearParams { session_id };
        let Ok(value) = serde_json::to_value(params) else {
            return;
        };
        let event = match client.request(method::SESSION_HISTORY_CLEAR, value).await {
            Ok(result) => cleared(result, seed),
            Err(error) => CommandEvent::Error(format!("Failed to clear history: {error}")),
        };
        let _ = tx.try_send(event);
    });
}

/// Read a `session/history/clear` response: the server replaced the session, so
/// the client has to adopt the new id or every later prompt is rejected.
fn cleared(result: Value, seed: Option<QueuedPrompt>) -> CommandEvent {
    match result
        .get("state")
        .cloned()
        .and_then(|state| serde_json::from_value::<PublicSessionState>(state).ok())
    {
        Some(state) => CommandEvent::Cleared {
            session_id: state.session.id,
            usage: state.session.token_usage,
            seed,
        },
        None => CommandEvent::Error("Clearing the history returned no session state.".to_owned()),
    }
}

/// Adopt the session `session/history/clear` created, on the main thread.
pub fn apply_cleared(app: &mut App, session_id: String, usage: Option<TokenUsage>) {
    app.terminal_notifier.set_default_title("");
    app.session.session_id = Some(session_id);
    app.session.active_turn_id = None;
    // Python `reset_usage_baseline` on the clear adopt, falling back to the
    // stats still held when the fresh state carries no usage.
    let baseline = usage.unwrap_or_else(|| crate::session_exit::current_usage(app));
    app.session.usage_baseline = Some(baseline);
}

/// Send the prompt that came with `/clear <prompt>`, once the new id is live.
pub fn start_seed_turn(client: &Arc<Client>, session_id: String, seed: QueuedPrompt) {
    let client = client.clone();
    tokio::spawn(async move {
        super::submission::start_turn(client, session_id, seed).await;
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clearing_adopts_the_replacement_session() {
        let result = serde_json::json!({
            "state": {
                "eventId": 1,
                "session": {
                    "id": "new-session",
                    "tokenUsage": {"inputTokens": 30, "outputTokens": 12, "totalTokens": 42},
                },
                "history": []
            },
        });
        let seed = QueuedPrompt {
            message_id: "rs-1".into(),
            text: "hi".into(),
        };
        let CommandEvent::Cleared {
            session_id,
            usage,
            seed,
        } = cleared(result, Some(seed))
        else {
            panic!("expected a cleared event");
        };
        assert_eq!(session_id, "new-session");
        assert_eq!(seed.map(|seed| seed.text).as_deref(), Some("hi"));

        let mut app = App::default();
        app.session.session_id = Some("dead-session".into());
        app.session.active_turn_id = Some("turn-1".into());
        app.session.usage_baseline = Some(TokenUsage {
            input_tokens: 1000,
            output_tokens: 500,
        });
        apply_cleared(&mut app, session_id, usage);
        assert_eq!(app.session.session_id.as_deref(), Some("new-session"));
        assert!(app.session.active_turn_id.is_none());
        // The clear adopt restarts the usage delta from the fresh session.
        assert_eq!(
            app.session.usage_baseline,
            Some(TokenUsage {
                input_tokens: 30,
                output_tokens: 12
            })
        );
    }

    #[test]
    fn a_response_without_state_is_an_error() {
        assert!(matches!(
            cleared(serde_json::json!({}), None),
            CommandEvent::Error(_)
        ));
    }
}
