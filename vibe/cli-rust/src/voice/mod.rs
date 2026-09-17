//! Voice recording state machine (Python `VoiceManager`).

#[cfg(feature = "voice")]
pub mod audio_recorder;
pub mod keyring;
pub mod transcribe;

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Arc;

use tokio::sync::mpsc::Sender;

/// Recording lifecycle, mirrors Python `TranscribeState`.
#[derive(Clone, Copy, PartialEq, Eq, Default)]
pub enum TranscribeState {
    #[default]
    Idle,
    Recording,
    Flushing,
}

/// Projected transcription config from `config/read` (Python `TranscriptionConfigView`).
#[derive(Clone)]
pub struct TranscriptionConfig {
    pub name: String,
    pub sample_rate: u32,
    pub encoding: String,
    pub target_streaming_delay_ms: u64,
    pub api_base: String,
    pub api_key_env_var: String,
}

/// UI-bound updates emitted by the recording pipeline (Python listener callbacks).
pub enum VoiceEvent {
    /// The pipeline finished draining and returned to `Idle`.
    Finished,
    TextDelta(String),
    Error(String),
    Notice(String),
}

/// Handle to the in-flight recording; the flags steer the background task.
pub struct Recording {
    stop: Arc<AtomicBool>,
    cancel: Arc<AtomicBool>,
    task: tokio::task::JoinHandle<()>,
}

impl Recording {
    /// Signal end-of-audio; the pipeline flushes and later emits `Finished`.
    pub fn stop(&self) {
        self.stop.store(true, Ordering::Relaxed);
    }

    /// Abort immediately without waiting for a transcript.
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::Relaxed);
        self.stop.store(true, Ordering::Relaxed);
        self.task.abort();
    }
}

/// Resolve the API key: env var first, then the macOS Keychain (Python `resolve_api_key`).
#[cfg(feature = "voice")]
fn resolve_api_key(env_var: &str) -> Option<String> {
    if env_var.is_empty() {
        return None;
    }
    if let Some(value) = std::env::var(env_var).ok().filter(|v| !v.is_empty()) {
        return Some(value);
    }
    keyring::get_api_key_from_keyring(env_var)
}

/// Start capture + transcription (Python `VoiceManager.start_recording`).
#[cfg(feature = "voice")]
pub fn start(
    cfg: &TranscriptionConfig,
    peak: Arc<AtomicU32>,
    events: Sender<VoiceEvent>,
) -> Result<Recording, String> {
    let Some(api_key) = resolve_api_key(&cfg.api_key_env_var) else {
        tracing::warn!(env_var = %cfg.api_key_env_var, "no transcription API key");
        return Err(format!(
            "Voice transcription needs an API key: set {}",
            cfg.api_key_env_var
        ));
    };

    let stop = Arc::new(AtomicBool::new(false));
    let cancel = Arc::new(AtomicBool::new(false));
    peak.store(0.0_f32.to_bits(), Ordering::Relaxed);

    let (chunks_rx, sample_rate) = audio_recorder::start(cfg.sample_rate, peak, stop.clone())
        .inspect_err(|error| tracing::warn!(%error, "audio recording failed to start"))?;

    let cfg = cfg.clone();
    let task_cancel = cancel.clone();
    let task = tokio::spawn(async move {
        let result = transcribe::transcribe(&cfg, &api_key, sample_rate, chunks_rx, &events).await;
        if task_cancel.load(Ordering::Relaxed) {
            return;
        }
        if let Err(msg) = result {
            tracing::warn!(error = %msg, "transcription failed");
            let _ = events.try_send(VoiceEvent::Error(msg));
        }
        let _ = events.try_send(VoiceEvent::Finished);
    });

    Ok(Recording { stop, cancel, task })
}

/// Stub used when the `voice` feature is disabled: capture is unavailable.
///
/// The full pipeline pulls in `cpal`, whose Linux backend needs the ALSA
/// system library via pkg-config (absent from the CI image). Builds without
/// the feature skip audio capture entirely.
#[cfg(not(feature = "voice"))]
pub fn start(
    _cfg: &TranscriptionConfig,
    _peak: Arc<AtomicU32>,
    _events: Sender<VoiceEvent>,
) -> Result<Recording, String> {
    Err("Voice capture is not available in this build.".to_string())
}
