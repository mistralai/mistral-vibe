//! Voice recording state machine (Python `VoiceManager`).

#[cfg(feature = "voice")]
pub mod audio_recorder;
pub mod signal;
pub mod tracking;
pub mod transcribe;

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Arc;
use std::time::Instant;

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
    /// The transcription server opened its session, naming it with `request_id`.
    SessionCreated(String),
    TextDelta(String),
    /// The pipeline failed and returned to `Idle`; never followed by `Finished`.
    Error(String),
    Notice(String),
}

/// Handle to the in-flight recording; the flags steer the background task.
pub struct Recording {
    stop: Arc<AtomicBool>,
    signal: Arc<signal::RecordingSignal>,
    started: Instant,
    cancel: Arc<AtomicBool>,
    task: tokio::task::JoinHandle<()>,
}

impl Recording {
    /// Signal end-of-audio; the pipeline flushes and later emits `Finished`.
    pub fn stop(&self) {
        self.signal.stop(self.started.elapsed());
        self.stop.store(true, Ordering::Relaxed);
    }

    /// Abort immediately without waiting for a transcript.
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::Relaxed);
        self.stop.store(true, Ordering::Relaxed);
        self.task.abort();
    }
}

/// Start capture + transcription (Python `VoiceManager.start_recording`).
#[cfg(feature = "voice")]
pub fn start(
    cfg: &TranscriptionConfig,
    peak: Arc<AtomicU32>,
    events: Sender<VoiceEvent>,
) -> Result<Recording, String> {
    let Some(api_key) = crate::credentials::resolve_api_key(&cfg.api_key_env_var) else {
        tracing::warn!(env_var = %cfg.api_key_env_var, "no transcription API key");
        return Err(format!(
            "Voice transcription needs an API key: set {}",
            cfg.api_key_env_var
        ));
    };

    let stop = Arc::new(AtomicBool::new(false));
    let cancel = Arc::new(AtomicBool::new(false));
    let signal = Arc::new(signal::RecordingSignal::default());
    peak.store(0.0_f32.to_bits(), Ordering::Relaxed);

    let (chunks_rx, sample_rate) =
        audio_recorder::start(cfg.sample_rate, peak, stop.clone(), signal.clone())
            .inspect_err(|error| tracing::warn!(%error, "audio recording failed to start"))?;

    let cfg = cfg.clone();
    let task_cancel = cancel.clone();
    let task_stop = stop.clone();
    let task_signal = signal.clone();
    let task = tokio::spawn(async move {
        let result = transcribe::transcribe(&cfg, &api_key, sample_rate, chunks_rx, &events).await;
        if task_cancel.load(Ordering::Relaxed) {
            return;
        }
        task_stop.store(true, Ordering::Relaxed);
        let event = match result {
            Ok(got_text) => task_signal.completion(got_text, std::env::consts::OS),
            Err(msg) => {
                tracing::warn!(error = %msg, "transcription failed");
                Some(VoiceEvent::Error(msg))
            }
        };
        // `Error` already returns the app to idle; a trailing `Finished` could
        // clobber a recording started in between.
        if let Some(event @ VoiceEvent::Error(_)) = event {
            let _ = events.send(event).await;
            return;
        }
        if let Some(event) = event {
            let _ = events.send(event).await;
        }
        let _ = events.send(VoiceEvent::Finished).await;
    });

    Ok(Recording {
        stop,
        signal,
        started: Instant::now(),
        cancel,
        task,
    })
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
