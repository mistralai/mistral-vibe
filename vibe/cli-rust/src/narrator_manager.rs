//! The narrator's speaking half: TTS, playback and read-aloud telemetry.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Instant;

use serde_json::{json, Value};
use tokio::sync::mpsc::Sender;
use tokio::task::AbortHandle;

use crate::app::App;
use crate::audio_player::{self, wav, AudioError};
use crate::credentials::resolve_api_key;
use crate::telemetry::{self, bmap, event};
use crate::tts::{self, SpeechConfig};
use crate::turn_summary::{Event, NarratorState};

/// The clip being synthesized or played; `id` tells its events from a stopped clip's.
pub struct Speech {
    id: u64,
    stop: Arc<AtomicBool>,
    task: AbortHandle,
}

/// Python `ReadAloudTrackingState`.
#[derive(Default)]
pub struct ReadAloudTracking {
    pub session_id: String,
    requested_at: Option<Instant>,
    play_started_at: Option<Instant>,
}

impl ReadAloudTracking {
    pub fn reset(&mut self) {
        self.session_id = uuid::Uuid::new_v4().to_string();
        self.requested_at = Some(Instant::now());
        self.play_started_at = None;
    }

    pub fn mark_play_started(&mut self) {
        self.play_started_at = Some(Instant::now());
    }

    pub fn time_to_first_read_s(&self) -> f64 {
        match (self.requested_at, self.play_started_at) {
            (Some(requested), Some(started)) => started.duration_since(requested).as_secs_f64(),
            _ => 0.0,
        }
    }

    pub fn elapsed_since_play_s(&self) -> f64 {
        self.play_started_at
            .map_or(0.0, |started| started.elapsed().as_secs_f64())
    }
}

/// The TTS settings, refreshed with every runtime snapshot (Python `sync`).
pub fn apply_runtime(app: &mut App, runtime: &Value) {
    app.narrator.speech_config = SpeechConfig::from_runtime(runtime);
}

/// Python `_on_read_aloud_requested`.
pub fn on_read_aloud_requested(app: &mut App) {
    app.narrator.tracking.reset();
    telemetry::record(
        app,
        event::READ_ALOUD_REQUESTED,
        bmap(json!({
            "read_aloud_session_id": app.narrator.tracking.session_id,
            "trigger": "autoplay_next_message",
        })),
    );
}

/// Python `_on_read_aloud_ended`.
pub fn on_read_aloud_ended(app: &App, status: &str, error_type: Option<&str>) {
    telemetry::record(
        app,
        event::READ_ALOUD_ENDED,
        bmap(json!({
            "read_aloud_session_id": app.narrator.tracking.session_id,
            "status": status,
            "error_type": error_type,
            "speed_selection": null,
            "elapsed_seconds": app.narrator.tracking.elapsed_since_play_s(),
        })),
    );
}

/// Python `_on_turn_summary`'s speak branch; the row reads `summarizing` until audio starts.
pub fn speak(app: &mut App, text: String) {
    stop(app);
    let (Some(cfg), Some(tx), Some(session_id)) = (
        app.narrator.speech_config.clone(),
        app.narrator.tx.clone(),
        app.session.session_id.clone(),
    ) else {
        app.narrator.state = NarratorState::Idle;
        return;
    };
    app.narrator.speech_id += 1;
    let id = app.narrator.speech_id;
    let stop = Arc::new(AtomicBool::new(false));
    let task_stop = stop.clone();
    let task = tokio::spawn(async move {
        let event = match speak_summary(&cfg, &session_id, &text, task_stop, &tx, id).await {
            Ok(()) => Event::Finished { id },
            Err(error) => {
                tracing::warn!(error = %error.message, "TTS speak failed");
                Event::Failed {
                    id,
                    error_type: error.error_type,
                }
            }
        };
        let _ = tx.send(event).await;
    });
    app.narrator.speech = Some(Speech {
        id,
        stop,
        task: task.abort_handle(),
    });
}

/// Python `_speak_summary`, checking the device before paying for the TTS request.
async fn speak_summary(
    cfg: &SpeechConfig,
    session_id: &str,
    text: &str,
    stop: Arc<AtomicBool>,
    tx: &Sender<Event>,
    id: u64,
) -> Result<(), AudioError> {
    let env_var = cfg.api_key_env_var.clone();
    let (available, api_key) = tokio::task::spawn_blocking(move || {
        (audio_player::check_available(), resolve_api_key(&env_var))
    })
    .await
    .map_err(|e| AudioError::backend(e.to_string()))?;
    available?;
    let metadata = tts::audio_request_metadata(session_id);
    let audio = tts::speak(cfg, &api_key.unwrap_or_default(), text, metadata).await?;
    let pcm = wav::decode_wav(&audio).map_err(AudioError::decode)?;
    let done = audio_player::play(pcm, stop).await?;
    let _ = tx.send(Event::Speaking { id }).await;
    done.await.unwrap_or(Ok(()))
}

/// Python `cancel`'s playback half: stop the clip and orphan its pending events.
pub fn stop(app: &mut App) {
    if let Some(speech) = app.narrator.speech.take() {
        speech.stop.store(true, Ordering::Relaxed);
        speech.task.abort();
    }
}

/// Python `_speak_summary` and `_on_playback_finished` state changes; stopped clips are ignored.
pub fn apply_event(app: &mut App, event: Event) {
    let current = |app: &App, id: u64| app.narrator.speech.as_ref().is_some_and(|s| s.id == id);
    match event {
        Event::Speaking { id } if current(app, id) => {
            app.narrator.state = NarratorState::Speaking;
            app.narrator.frame = 0;
            app.narrator.tracking.mark_play_started();
            telemetry::record(
                app,
                event::READ_ALOUD_PLAY_STARTED,
                bmap(json!({
                    "read_aloud_session_id": app.narrator.tracking.session_id,
                    "time_to_first_read_s": app.narrator.tracking.time_to_first_read_s(),
                    "speed_selection": null,
                })),
            );
        }
        Event::Finished { id } if current(app, id) => {
            app.narrator.speech = None;
            if app.narrator.state == NarratorState::Speaking {
                on_read_aloud_ended(app, "completed", None);
                app.narrator.state = NarratorState::Idle;
            }
        }
        Event::Failed { id, error_type } if current(app, id) => {
            app.narrator.speech = None;
            on_read_aloud_ended(app, "error", Some(error_type));
            app.narrator.state = NarratorState::Idle;
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests;
