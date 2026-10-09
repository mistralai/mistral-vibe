//! Voice settings drafts and app-server persistence, mirroring Python's VoiceApp.

use std::collections::BTreeMap;
use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::{json, Value};

use crate::app::{App, ToastSeverity};
use crate::commands::{submission::new_message_id, CommandEvent};
use crate::config_write;
use crate::server::{method, Client, TelemetryRecordParams};
use crate::transcript::local;

pub const LABELS: [&str; 2] = ["Voice mode", "Narrator (experimental)"];
const PATHS: [&str; 2] = ["/voice_mode_enabled", "/narrator_enabled"];

#[derive(Default)]
pub struct VoiceApp {
    pub open: bool,
    pub selected: usize,
    pub values: [bool; 2],
    changes: [bool; 2],
}

pub fn open(app: &mut App) {
    local::add_command_result(
        &mut app.view.transcript,
        &new_message_id(),
        "Voice settings opened...",
    );
    app.voice_app = VoiceApp {
        open: true,
        values: [
            app.voice.mode_enabled,
            app.session.startup_config.narrator_enabled,
        ],
        ..VoiceApp::default()
    };
}

pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER)
    {
        return;
    }
    match key.code {
        KeyCode::Esc => close(app, client),
        KeyCode::Up | KeyCode::Down | KeyCode::Char('j' | 'k') => {
            let down = matches!(key.code, KeyCode::Down | KeyCode::Char('j'));
            let selected = app.voice_app.selected;
            app.voice_app.selected = crate::list_nav::wrap(selected, LABELS.len(), down);
        }
        KeyCode::Enter | KeyCode::Char(' ') => {
            let picker = &mut app.voice_app;
            picker.values[picker.selected] = !picker.values[picker.selected];
            picker.changes[picker.selected] = true;
        }
        _ => {}
    }
}

fn close(app: &mut App, client: &Arc<Client>) {
    app.voice_app.open = false;
    let ops: Vec<Value> = PATHS
        .iter()
        .enumerate()
        .filter(|(i, _)| app.voice_app.changes[*i])
        .map(|(i, path)| config_write::set_op(path, app.voice_app.values[i]))
        .collect();
    if ops.is_empty() {
        local::add_command_result(
            &mut app.view.transcript,
            &new_message_id(),
            "Voice settings closed (no changes saved).",
        );
        return;
    }
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.command_tx.clone())
    else {
        return;
    };
    let previous_enabled = app.voice.mode_enabled;
    let enabling_audio = app
        .voice_app
        .changes
        .iter()
        .zip(app.voice_app.values)
        .any(|(changed, value)| *changed && value);
    let pending = app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let result =
            config_write::write(&client, &session_id, ops, "app-server config update").await;
        let event = match result {
            Ok(runtime) => {
                if let Some(enabled) = runtime
                    .pointer("/runtime/config/voiceModeEnabled")
                    .and_then(Value::as_bool)
                {
                    if enabled != previous_enabled {
                        tokio::spawn(record_toggle(client.clone(), session_id, enabled));
                    }
                }
                CommandEvent::VoiceSettings {
                    runtime,
                    previous_enabled,
                    enabling_audio,
                }
            }
            Err(error) => failed(error),
        };
        crate::input::deliver(Some(tx), event, &pending).await;
    });
}

fn failed(error: String) -> CommandEvent {
    CommandEvent::Error(format!("Failed to apply: voice settings — {error}"))
}

async fn record_toggle(client: Arc<Client>, session_id: String, enabled: bool) {
    let params = TelemetryRecordParams {
        session_id,
        name: "vibe.voice_mode_toggled".to_owned(),
        properties: BTreeMap::from([("enabled".to_owned(), json!(enabled))]),
        correlate_last_request: false,
    };
    if let Ok(params) = serde_json::to_value(params) {
        let _ = client.request(method::TELEMETRY_RECORD, params).await;
    }
}

pub fn apply_saved(app: &mut App, runtime: &Value, previous_enabled: bool, enabling_audio: bool) {
    crate::event_handler::apply_runtime_value(app, runtime);
    if app.voice.mode_enabled != previous_enabled {
        let message = if app.voice.mode_enabled {
            "Voice mode enabled. Press **Ctrl+R** to start recording."
        } else {
            "Voice mode disabled."
        };
        local::add_command_result(&mut app.view.transcript, &new_message_id(), message);
    }
    if enabling_audio && !cfg!(feature = "voice") {
        app.show_toast(
            "Audio setting saved, but audio is unavailable: Voice capture is not available in this build.".to_owned(),
            ToastSeverity::Warning,
            15,
        );
    }
}

pub fn apply_runtime(app: &mut App, runtime: &Value) {
    if let Some(enabled) = runtime
        .pointer("/runtime/config/voiceModeEnabled")
        .and_then(Value::as_bool)
    {
        app.voice.mode_enabled = enabled;
        if !enabled {
            app.cancel_recording();
        }
    }
}
