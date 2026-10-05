//! Client telemetry emission matches the Python emitters' once-per-action contract.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::approval;
use vibe_rs::message_queue::{self, QueueItem};
use vibe_rs::server::{ApprovalCallback, Client};
use vibe_rs::telemetry::{self, event, TelemetryEvent};
use vibe_rs::voice::{TranscribeState, TranscriptionConfig, VoiceEvent};
use vibe_rs::{config, config_fields};

fn app_with_telemetry() -> (App, mpsc::Receiver<TelemetryEvent>) {
    let mut app = App::default();
    let (tx, rx) = mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
    app.telemetry_tx = Some(tx);
    (app, rx)
}

fn drained(rx: &mut mpsc::Receiver<TelemetryEvent>) -> Vec<TelemetryEvent> {
    std::iter::from_fn(|| rx.try_recv().ok()).collect()
}

fn names(events: &[TelemetryEvent]) -> Vec<&'static str> {
    events.iter().map(|event| event.name).collect()
}

#[test]
fn failed_transcription_records_error_only_and_settles_idle() {
    let (mut app, mut rx) = app_with_telemetry();
    app.voice.transcribe_state = TranscribeState::Flushing;
    app.voice.tracking.start();
    app.apply_voice_event(VoiceEvent::Error("socket closed".into()));
    assert_eq!(names(&drained(&mut rx)), [event::TRANSCRIPTION_ERROR]);
    assert!(app.voice.transcribe_state == TranscribeState::Idle);
}

#[test]
fn finished_transcription_records_done() {
    let (mut app, mut rx) = app_with_telemetry();
    app.voice.transcribe_state = TranscribeState::Flushing;
    app.voice.tracking.start();
    app.apply_voice_event(VoiceEvent::Finished);
    assert_eq!(names(&drained(&mut rx)), [event::TRANSCRIPTION_DONE]);
}

#[tokio::test]
async fn recording_start_failure_records_nothing() {
    let (mut app, mut rx) = app_with_telemetry();
    let (voice_tx, _voice_rx) = mpsc::channel::<VoiceEvent>(4);
    app.voice.tx = Some(voice_tx);
    app.voice.transcription = Some(TranscriptionConfig {
        name: "voxtral".into(),
        sample_rate: 16000,
        encoding: "pcm_s16le".into(),
        target_streaming_delay_ms: 500,
        api_base: "wss://example.invalid".into(),
        // No key source: the start fails before any capture, in every build.
        api_key_env_var: String::new(),
    });
    app.toggle_recording();
    assert!(app.voice.transcribe_state == TranscribeState::Idle);
    assert!(drained(&mut rx).is_empty());
}

fn sent_item(mentions: Option<Value>) -> QueueItem {
    merged_item("message-1", mentions)
}

/// A prompt folded into the group whose server entry is `message-1`.
fn merged_item(message_id: &str, mentions: Option<Value>) -> QueueItem {
    QueueItem {
        queue_item_id: Some("queue-1".into()),
        message_id: message_id.into(),
        server_message_id: "message-1".into(),
        text: "@src/main.rs explain".into(),
        images: Vec::new(),
        mentions,
        sent: true,
        ever_sent: true,
        revision: 1,
        replacing: false,
    }
}

#[tokio::test]
async fn mentions_are_reported_once_when_the_prompt_runs() {
    let (mut app, mut rx) = app_with_telemetry();
    let client = Arc::new(Client::stub());
    let mentions = json!({"count": 1, "contextTypes": {"file": 1}, "fileExtensions": {"rs": 1}});
    app.queue.items.push(sent_item(Some(mentions)));
    assert!(
        drained(&mut rx).is_empty(),
        "queueing alone must not report"
    );

    message_queue::turn_started(&mut app, &client, "queue-1");

    let events = drained(&mut rx);
    assert_eq!(names(&events), [event::AT_MENTION_INSERTED]);
    assert_eq!(events[0].properties["nb_mentions"], 1);
    assert_eq!(events[0].properties["message_id"], "message-1");
    assert_eq!(events[0].properties["file_extensions"], json!({"rs": 1}));
}

#[tokio::test]
async fn merged_prompts_report_their_own_message_ids() {
    let (mut app, mut rx) = app_with_telemetry();
    let client = Arc::new(Client::stub());
    let mentions = json!({"count": 1, "contextTypes": {"file": 1}});
    app.queue
        .items
        .push(merged_item("message-1", Some(mentions.clone())));
    app.queue
        .items
        .push(merged_item("message-2", Some(mentions)));

    message_queue::turn_started(&mut app, &client, "queue-1");

    let ids: Vec<_> = drained(&mut rx)
        .into_iter()
        .map(|event| event.properties["message_id"].clone())
        .collect();
    assert_eq!(ids, [json!("message-1"), json!("message-2")]);
}

#[tokio::test]
async fn prompts_without_mentions_report_nothing() {
    let (mut app, mut rx) = app_with_telemetry();
    let client = Arc::new(Client::stub());
    app.queue.items.push(sent_item(Some(json!({"count": 0}))));
    message_queue::turn_started(&mut app, &client, "queue-1");
    assert!(drained(&mut rx).is_empty());
}

fn loaded_with_voice(enabled: bool) -> config_fields::Loaded {
    config_fields::parse(&json!({"fields": [{
        "name": "voice_mode_enabled", "path": "/voice_mode_enabled",
        "kind": "bool", "value": enabled,
    }]}))
}

#[test]
fn config_reload_that_flips_voice_mode_records_the_toggle() {
    let (mut app, mut rx) = app_with_telemetry();
    config::apply_loaded(&mut app, loaded_with_voice(false));
    assert!(
        drained(&mut rx).is_empty(),
        "the first load is not a toggle"
    );

    config::apply_loaded(&mut app, loaded_with_voice(false));
    assert!(
        drained(&mut rx).is_empty(),
        "an unchanged reload is not a toggle"
    );

    config::apply_loaded(&mut app, loaded_with_voice(true));
    let events = drained(&mut rx);
    assert_eq!(names(&events), [event::VOICE_MODE_TOGGLED]);
    assert_eq!(events[0].properties["enabled"], true);
}

#[tokio::test]
async fn stop_session_sends_queued_events_before_session_stop() {
    let (app, mut rx) = app_with_telemetry();
    telemetry::slash_command_used(&app, "/exit", "builtin");
    telemetry::user_cancelled_action(&app, "interrupt_agent");
    let (client, mut frames) = Client::stub_answering();

    vibe_rs::event_loop::stop_session(&client, "session-1", &mut rx).await;

    let sent: Vec<_> = std::iter::from_fn(|| frames.try_recv().ok())
        .map(|frame| {
            let name = frame
                .pointer("/params/name")
                .cloned()
                .unwrap_or(Value::Null);
            (frame["method"].clone(), name)
        })
        .collect();
    assert_eq!(
        sent,
        [
            (json!("telemetry/record"), json!(event::SLASH_COMMAND_USED)),
            (
                json!("telemetry/record"),
                json!(event::USER_CANCELLED_ACTION)
            ),
            (json!("session/stop"), Value::Null),
        ]
    );
}

#[test]
fn async_effect_mentions_go_through_the_bounded_queue() {
    let (app, mut rx) = app_with_telemetry();
    let mentions = json!({"count": 1, "contextTypes": {"file": 1}});
    for _ in 0..telemetry::TELEMETRY_CHANNEL_CAP + 8 {
        telemetry::record_mentions(app.telemetry_tx.as_ref(), Some(&mentions), "message-1");
    }
    assert_eq!(drained(&mut rx).len(), telemetry::TELEMETRY_CHANNEL_CAP);
}

#[test]
fn transcription_start_waits_for_the_server_session_id() {
    let (mut app, mut rx) = app_with_telemetry();
    app.voice.transcribe_state = TranscribeState::Recording;
    app.voice.tracking.start();
    assert!(drained(&mut rx).is_empty());

    app.apply_voice_event(VoiceEvent::SessionCreated("request-1".into()));
    app.apply_voice_event(VoiceEvent::Error("socket closed".into()));

    let events = drained(&mut rx);
    assert_eq!(
        names(&events),
        [event::TRANSCRIPTION_START, event::TRANSCRIPTION_ERROR]
    );
    assert_eq!(events[0].properties["recording_id"], "request-1");
    assert_eq!(events[1].properties["recording_id"], "request-1");
}

#[test]
fn session_created_after_cancel_records_nothing() {
    let (mut app, mut rx) = app_with_telemetry();
    app.voice.transcribe_state = TranscribeState::Idle;
    app.apply_voice_event(VoiceEvent::SessionCreated("request-1".into()));
    assert!(drained(&mut rx).is_empty());
}

fn app_with_open_approval() -> (
    App,
    mpsc::Receiver<TelemetryEvent>,
    mpsc::Receiver<approval::Event>,
) {
    let (mut app, rx) = app_with_telemetry();
    app.session.status = Status::Ready;
    let (approval_tx, approval_rx) = mpsc::channel(4);
    app.approval.tx = Some(approval_tx);
    let callback: ApprovalCallback = serde_json::from_value(json!({
        "callbackId": "approval-1",
        "sessionId": "session-1",
        "detail": {"kind": "approval"},
    }))
    .unwrap();
    app.approval.enqueue(callback).unwrap();
    approval::show_pending(&mut app);
    app.approval.mount_time = None;
    (app, rx, approval_rx)
}

#[tokio::test]
async fn every_deny_key_records_reject_approval() {
    let deny_keys = [
        (0, KeyCode::Char('n')),
        (0, KeyCode::Char('4')),
        (0, KeyCode::Esc),
        (3, KeyCode::Enter),
    ];
    for (selected, code) in deny_keys {
        let key = KeyEvent::new(code, KeyModifiers::NONE);
        let (mut app, mut rx, mut answers) = app_with_open_approval();
        app.approval.selected = selected;
        let client = Arc::new(Client::stub());
        approval::handle_key(&mut app, &client, key);
        assert!(answers.recv().await.is_some(), "{key:?} sends the deny");
        let events = drained(&mut rx);
        assert_eq!(names(&events), [event::USER_CANCELLED_ACTION], "{key:?}");
        assert_eq!(events[0].properties["action"], "reject_approval");
    }
}

#[tokio::test]
async fn approving_records_nothing() {
    let (mut app, mut rx, mut answers) = app_with_open_approval();
    let client = Arc::new(Client::stub());
    approval::handle_key(
        &mut app,
        &client,
        KeyEvent::new(KeyCode::Char('y'), KeyModifiers::NONE),
    );
    assert!(answers.recv().await.is_some(), "y sends the approval");
    assert!(drained(&mut rx).is_empty());
}
