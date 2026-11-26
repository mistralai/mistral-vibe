//! Recording-wide signal detection and microphone access diagnostics.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

use super::VoiceEvent;

const SILENCE_PEAK_THRESHOLD: f32 = 0.001;
const MIN_SIGNAL_RECORDING_DURATION_MS: u64 = 500;

#[derive(Default)]
pub struct RecordingSignal {
    duration_ms: AtomicU64,
    has_signal: AtomicBool,
}

impl RecordingSignal {
    pub fn observe(&self, peak: f32) {
        if peak > SILENCE_PEAK_THRESHOLD {
            self.has_signal.store(true, Ordering::Relaxed);
        }
    }

    pub fn stop(&self, duration: Duration) {
        self.duration_ms
            .store(duration.as_millis() as u64, Ordering::Relaxed);
    }

    pub fn completion(&self, got_text: bool, platform: &str) -> Option<VoiceEvent> {
        if got_text {
            return None;
        }
        if !self.has_signal.load(Ordering::Relaxed)
            && self.duration_ms.load(Ordering::Relaxed) >= MIN_SIGNAL_RECORDING_DURATION_MS
        {
            return Some(VoiceEvent::Error(format!(
                "No audio detected from microphone — check your terminal has mic access.{}",
                mic_access_hint(platform),
            )));
        }
        Some(VoiceEvent::Notice("No speech detected".to_owned()))
    }
}

pub fn mic_access_hint(platform: &str) -> &'static str {
    match platform {
        "macos" => " Grant access in System Settings → Privacy & Security → Microphone.",
        "windows" => " Grant access in Settings → Privacy & security → Microphone.",
        _ => "",
    }
}
