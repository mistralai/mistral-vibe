//! Narration playback on the default output device (Python `audio_player`).

#[cfg(feature = "voice")]
mod output;
pub mod source;
pub mod wav;

#[cfg(feature = "voice")]
pub use output::{check_available, play};

/// Whether this build can play audio at all (cpal ships with `voice`).
pub const SUPPORTED: bool = cfg!(feature = "voice");

/// A narration failure named after Python's exception, for `vibe.read_aloud.ended`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AudioError {
    pub error_type: &'static str,
    pub message: String,
}

impl AudioError {
    pub fn new(error_type: &'static str, message: String) -> Self {
        Self {
            error_type,
            message,
        }
    }

    pub fn no_device() -> Self {
        Self {
            error_type: "NoAudioOutputDeviceError",
            message: "No audio output device available".to_owned(),
        }
    }

    pub fn backend(message: String) -> Self {
        Self {
            error_type: "AudioBackendUnavailableError",
            message,
        }
    }

    /// Python's `wave.Error` and `binascii.Error` both report as `Error`.
    pub fn decode(message: String) -> Self {
        Self {
            error_type: "Error",
            message,
        }
    }
}

/// Builds without `voice` (no cpal) cannot play audio.
#[cfg(not(feature = "voice"))]
pub fn check_available() -> Result<(), AudioError> {
    Err(AudioError::backend(
        "Audio playback is not available in this build.".to_owned(),
    ))
}

#[cfg(not(feature = "voice"))]
pub async fn play(
    _pcm: wav::Pcm,
    _stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Result<tokio::sync::oneshot::Receiver<Result<(), AudioError>>, AudioError> {
    Err(check_available().unwrap_err())
}
