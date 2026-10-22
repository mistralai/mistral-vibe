//! Client-side event recording.

use std::collections::BTreeMap;
use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::mpsc::error::TrySendError;

use crate::app::App;
use crate::server::{method, Client, TelemetryRecordParams};

/// Queued events awaiting the event loop. Overflow drops the emission attempt:
/// telemetry must never stall the UI thread or grow without bound.
pub const TELEMETRY_CHANNEL_CAP: usize = 64;

pub type TelemetrySender = tokio::sync::mpsc::Sender<TelemetryEvent>;

pub struct TelemetryEvent {
    pub name: &'static str,
    pub properties: BTreeMap<String, Value>,
}

/// Queue one event. Consent, base metadata and delivery are all server-owned,
/// so a failure here is never surfaced to the user.
pub fn record(app: &App, name: &'static str, properties: BTreeMap<String, Value>) {
    enqueue(app.telemetry_tx.as_ref(), name, properties);
}

/// `record` for async effects, which hold a clone of the sender instead of the app.
pub fn enqueue(
    tx: Option<&TelemetrySender>,
    name: &'static str,
    properties: BTreeMap<String, Value>,
) {
    let Some(tx) = tx else {
        return;
    };
    if let Err(error) = tx.try_send(TelemetryEvent { name, properties }) {
        match error {
            TrySendError::Full(_) => {
                tracing::warn!(name, "telemetry queue full; dropping event");
            }
            TrySendError::Closed(_) => {
                tracing::debug!(name, "telemetry channel closed; dropping event");
            }
        }
    }
}

/// Effect side: detached, so no caller ever waits on an analytics round-trip.
pub fn send(client: &Arc<Client>, session_id: &str, event: TelemetryEvent) {
    client.telemetry_spawned();
    let client = client.clone();
    let session_id = session_id.to_owned();
    tokio::spawn(async move {
        let answer = deliver(&client, &session_id, event);
        client.telemetry_written();
        answer.await;
    });
}

/// Send what is still queued, concurrently, so the last actions (such as
/// `/exit`) are not lost with the event loop.
pub async fn flush(
    client: &Client,
    session_id: &str,
    events: &mut tokio::sync::mpsc::Receiver<TelemetryEvent>,
) {
    let pending: Vec<_> = std::iter::from_fn(|| events.try_recv().ok())
        .map(|event| deliver(client, session_id, event))
        .collect();
    futures::future::join_all(pending).await;
}

/// The only place that touches the wire: writes the frame, returns the wait for its answer.
fn deliver(
    client: &Client,
    session_id: &str,
    event: TelemetryEvent,
) -> impl std::future::Future<Output = ()> + Send + 'static {
    let name = event.name;
    let params = TelemetryRecordParams {
        session_id: session_id.to_owned(),
        name: name.to_owned(),
        properties: event.properties,
        correlate_last_request: false,
    };
    let sent = serde_json::to_value(params)
        .map_err(anyhow::Error::from)
        .and_then(|value| client.send_now(method::TELEMETRY_RECORD, value));
    async move {
        let error = match sent {
            Ok(answer) => match answer.await {
                Ok(Ok(_)) => return,
                Ok(Err(failure)) => failure.to_string(),
                Err(_) => "response channel dropped".to_owned(),
            },
            Err(error) => error.to_string(),
        };
        tracing::debug!(%error, name, "telemetry record failed");
    }
}

pub fn slash_command_used(app: &App, command: &str, command_type: &str) {
    record(
        app,
        event::SLASH_COMMAND_USED,
        bmap(json!({
            "command": command.trim_start_matches('/'),
            "command_type": command_type,
        })),
    );
}

pub fn user_copied_text(app: &App, text: &str) {
    record(
        app,
        event::USER_COPIED_TEXT,
        bmap(json!({"text_length": text.chars().count()})),
    );
}

pub fn user_cancelled_action(app: &App, action: &str) {
    record(
        app,
        event::USER_CANCELLED_ACTION,
        bmap(json!({"action": action})),
    );
}

/// Recorded client-side for the legacy harness only; unified records it server-side.
pub fn session_branched(app: &App, source_session_id: &str, new_session_id: &str) {
    record(
        app,
        event::SESSION_BRANCHED,
        bmap(json!({
            "source_session_id": source_session_id,
            "new_session_id": new_session_id,
        })),
    );
}

pub fn voice_mode_toggled(app: &App, enabled: bool) {
    record(
        app,
        event::VOICE_MODE_TOGGLED,
        bmap(json!({"enabled": enabled})),
    );
}

/// The `@` mention event, queued once the prompt runs.
pub fn at_mention_inserted(app: &App, mentions: Option<&Value>, message_id: &str) {
    if let Some(properties) = mention_properties(mentions, message_id) {
        record(app, event::AT_MENTION_INSERTED, properties);
    }
}

/// `at_mention_inserted` for async effects that start the turn themselves.
pub fn record_mentions(tx: Option<&TelemetrySender>, mentions: Option<&Value>, message_id: &str) {
    if let Some(properties) = mention_properties(mentions, message_id) {
        enqueue(tx, event::AT_MENTION_INSERTED, properties);
    }
}

/// `vibe.at_mention_inserted` properties; `None` at zero mentions.
pub fn mention_properties(
    mentions: Option<&Value>,
    message_id: &str,
) -> Option<BTreeMap<String, Value>> {
    let mentions = mentions?;
    let count = mentions.get("count").and_then(Value::as_u64).unwrap_or(0);
    if count == 0 {
        return None;
    }
    let extensions = mentions.get("fileExtensions").filter(|value| {
        value
            .as_object()
            .is_some_and(|extensions| !extensions.is_empty())
    });
    Some(bmap(json!({
        "nb_mentions": count,
        "context_types": mentions.get("contextTypes"),
        "file_extensions": extensions,
        "message_id": message_id,
    })))
}

/// A `json!` object as the `properties` map of a `telemetry/record` request.
pub fn bmap(value: Value) -> BTreeMap<String, Value> {
    serde_json::from_value(value).unwrap_or_default()
}

/// Event names, identical across every Vibe emitter so the datalake sees one
/// stream per event regardless of which client produced it.
pub mod event {
    pub const STARTUP: &str = "vibe.startup";
    pub const SLASH_COMMAND_USED: &str = "vibe.slash_command_used";
    pub const AT_MENTION_INSERTED: &str = "vibe.at_mention_inserted";
    pub const USER_COPIED_TEXT: &str = "vibe.user_copied_text";
    pub const USER_CANCELLED_ACTION: &str = "vibe.user_cancelled_action";
    pub const SESSION_BRANCHED: &str = "vibe.session_branched";
    pub const VOICE_MODE_TOGGLED: &str = "vibe.voice_mode_toggled";
    pub const TRANSCRIPTION_START: &str = "vibe.audio.transcription.start";
    pub const TRANSCRIPTION_CANCEL: &str = "vibe.audio.transcription.cancel_recording";
    pub const TRANSCRIPTION_DONE: &str = "vibe.audio.transcription.done";
    pub const TRANSCRIPTION_ERROR: &str = "vibe.audio.transcription.error";
    pub const READ_ALOUD_REQUESTED: &str = "vibe.read_aloud.requested";
    pub const READ_ALOUD_PLAY_STARTED: &str = "vibe.read_aloud.play_started";
    pub const READ_ALOUD_ENDED: &str = "vibe.read_aloud.ended";
}
