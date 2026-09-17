//! The two rewind round-trips, answered on the main thread as a `rewind::Event`.

use std::sync::Arc;

use serde_json::Value;

use super::Event;
use crate::app::App;
use crate::server::{method, Client, PublicSessionState};

/// Ask whether rewinding to `messages[index]` would restore files; the panel
/// opens on the answer (Python `_select_rewind_widget`).
pub(super) fn select(
    app: &mut App,
    client: &Arc<Client>,
    messages: &[(usize, String, String)],
    index: usize,
) {
    let Some((entry_index, entry_id, preview)) = messages.get(index).cloned() else {
        return;
    };
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.rewind.tx.clone())
    else {
        return;
    };
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let params = serde_json::json!({"sessionId": session_id, "entryId": entry_id});
        let event = match client.request(method::SESSION_REWIND_READ, params).await {
            Ok(result) => Event::Selected {
                entry_index,
                entry_id,
                preview,
                has_file_changes: result
                    .get("hasFileChanges")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            },
            Err(error) => Event::Failed(error.to_string()),
        };
        crate::input::deliver(Some(tx), event, &pending).await;
    });
}

/// Send the rewind itself (Python `_execute_rewind`).
pub(super) fn confirm(app: &mut App, client: &Arc<Client>, inplace: bool) {
    let (Some(session_id), Some(entry_id), Some(tx)) = (
        app.session.session_id.clone(),
        app.rewind.entry_id.clone(),
        app.rewind.tx.clone(),
    ) else {
        return;
    };
    let restore_files = app.rewind.restore_files;
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let params = serde_json::json!({
            "sessionId": session_id.clone(),
            "entryId": entry_id,
            "restoreFiles": restore_files,
            "inplace": inplace,
        });
        let event = match client.request(method::SESSION_REWIND, params).await {
            Ok(result) => done(result, session_id, inplace),
            Err(error) => Event::Failed(error.to_string()),
        };
        crate::input::deliver(Some(tx), event, &pending).await;
    });
}

/// Read a `session/rewind` response into the event that applies it.
fn done(result: Value, old_session_id: String, inplace: bool) -> Event {
    let Some(state) = result
        .get("state")
        .cloned()
        .and_then(|state| serde_json::from_value::<PublicSessionState>(state).ok())
    else {
        return Event::Failed("Rewind returned no session state.".into());
    };
    Event::Done {
        message: result
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned(),
        restore_errors: result
            .get("restoreErrors")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect(),
        old_session_id,
        inplace,
        state: Box::new(state),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state(id: &str) -> Value {
        serde_json::json!({"eventId": 7, "session": {"id": id}, "history": []})
    }

    #[test]
    fn done_reads_the_rewind_response() {
        let result = serde_json::json!({
            "message": "hello",
            "restoreErrors": ["Failed to restore file: a.py"],
            "restoredPaths": ["a.py"],
            "state": state("new-session"),
        });
        let Event::Done {
            message,
            restore_errors,
            old_session_id,
            inplace,
            state,
        } = done(result, "old-session".into(), false)
        else {
            panic!("expected a done event");
        };
        assert_eq!(message, "hello");
        assert_eq!(restore_errors, ["Failed to restore file: a.py"]);
        assert_eq!(old_session_id, "old-session");
        assert!(!inplace);
        assert_eq!(state.session.id, "new-session");
    }

    #[test]
    fn done_fails_without_state() {
        let result = serde_json::json!({"message": "hello"});
        assert!(matches!(done(result, "old".into(), true), Event::Failed(_)));
    }
}
