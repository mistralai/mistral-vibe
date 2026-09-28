//! Feedback prompt state and app-server effects.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::Value;
use tokio::sync::mpsc::Sender;

use crate::app::App;
use crate::server::{
    method, Client, FeedbackAction, FeedbackRecordParams, FeedbackShouldShowParams,
    FeedbackShouldShowResponse, TelemetryRecordParams,
};

const THANK_YOU_DURATION: Duration = Duration::from_secs(2);
const RATING_EVENT: &str = "vibe.user_rating_feedback";

#[derive(Default, PartialEq)]
pub enum Message {
    #[default]
    Hidden,
    Prompt,
    ThankYou,
    Snoozed,
}

#[derive(Default)]
pub struct Feedback {
    pub message: Message,
    pub hide_at: Option<Instant>,
    pub snooze_duration_seconds: Option<u64>,
    checking: bool,
    pub tx: Option<Sender<Event>>,
}

pub enum Event {
    Checked {
        show: bool,
        snooze_duration_seconds: Option<u64>,
    },
    Recorded,
}

/// `session_id` pins the session the prompt belongs to: a mid-turn
/// `session/compacted` handoff may have replaced the current one by the time
/// a deferred check runs, but the check must stay with the submitted prompt.
pub fn maybe_show(app: &mut App, client: &Arc<Client>, session_id: Option<String>) {
    if app.feedback.checking || app.feedback.message != Message::Hidden {
        return;
    }
    let (Some(session_id), Some(tx)) = (session_id, app.feedback.tx.clone()) else {
        return;
    };
    app.feedback.checking = true;
    let pending = app.commit_started();
    let params = FeedbackShouldShowParams {
        session_id: session_id.clone(),
        pending_user_messages: 1,
    };
    // Python awaits this check inside turn handling, so its frame always lands
    // before a later event's requests (e.g. the silent incomplete-stream
    // retry's `turn/start`). Enqueue it here, not in spawn-poll order.
    let sent = client.send_now(
        method::FEEDBACK_SHOULD_SHOW,
        serde_json::to_value(params).unwrap_or(Value::Null),
    );
    let client = client.clone();
    tokio::spawn(async move {
        let (show, snooze_duration_seconds) = match sent {
            Ok(rx) => match rx.await {
                Ok(Ok(value)) => serde_json::from_value::<FeedbackShouldShowResponse>(value)
                    .ok()
                    .map_or((false, None), |response| {
                        (response.show, response.snooze_duration_seconds)
                    }),
                _ => (false, None),
            },
            Err(_) => (false, None),
        };
        if show {
            record_action(&client, &session_id, FeedbackAction::Asked).await;
        }
        crate::input::deliver(
            Some(tx),
            Event::Checked {
                show,
                snooze_duration_seconds,
            },
            &pending,
        )
        .await;
    });
}

pub fn apply_event(app: &mut App, event: Event) {
    app.commit_finished();
    match event {
        Event::Checked {
            show,
            snooze_duration_seconds,
        } => {
            app.feedback.checking = false;
            app.feedback.snooze_duration_seconds = snooze_duration_seconds;
            if show && app.feedback.message == Message::Hidden {
                app.feedback.message = Message::Prompt;
            }
        }
        Event::Recorded => {}
    }
}

pub fn rate(app: &mut App, client: &Arc<Client>, rating: u8) {
    if app.feedback.message != Message::Prompt {
        return;
    }
    app.feedback.message = Message::ThankYou;
    app.feedback.hide_at = Some(Instant::now() + THANK_YOU_DURATION);
    record(app, client, FeedbackAction::Given, Some(rating));
}

pub fn snooze(app: &mut App, client: &Arc<Client>) {
    if app.feedback.message != Message::Prompt {
        return;
    }
    app.feedback.message = Message::Snoozed;
    app.feedback.hide_at = Some(Instant::now() + THANK_YOU_DURATION);
    record(app, client, FeedbackAction::Snoozed, None);
}

pub fn hide(app: &mut App) {
    app.feedback.message = Message::Hidden;
    app.feedback.hide_at = None;
}

pub fn hide_expired(app: &mut App) {
    if app.feedback.hide_at.is_some_and(|at| Instant::now() >= at) {
        hide(app);
    }
}

fn record(app: &App, client: &Arc<Client>, action: FeedbackAction, rating: Option<u8>) {
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.feedback.tx.clone())
    else {
        return;
    };
    let version = app.session.startup_config.server_version.clone();
    let model = app.model_picker.current_model.clone();
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        if let Some(rating) = rating {
            let properties = BTreeMap::from([
                ("model".to_owned(), Value::String(model)),
                ("rating".to_owned(), Value::from(rating)),
                ("version".to_owned(), Value::String(version)),
            ]);
            let params = TelemetryRecordParams {
                session_id: session_id.clone(),
                name: RATING_EVENT.to_owned(),
                properties,
                correlate_last_request: true,
            };
            let _ = request(&client, method::TELEMETRY_RECORD, params).await;
        }
        record_action(&client, &session_id, action).await;
        crate::input::deliver(Some(tx), Event::Recorded, &pending).await;
    });
}

async fn record_action(client: &Client, session_id: &str, action: FeedbackAction) {
    let params = FeedbackRecordParams {
        session_id: session_id.to_owned(),
        action,
    };
    let _ = request(client, method::FEEDBACK_RECORD, params).await;
}

async fn request(client: &Client, method: &str, params: impl serde::Serialize) -> Option<Value> {
    let params = serde_json::to_value(params).ok()?;
    client.request(method, params).await.ok()
}
