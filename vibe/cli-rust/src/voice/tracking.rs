//! Per-recording analytics bookkeeping (Python `VoiceManager._tracking`).

use std::collections::BTreeMap;
use std::time::Instant;

use serde_json::{json, Value};

use crate::telemetry::bmap;

#[derive(Default)]
pub struct RecordingTracking {
    /// The transcription server's `request_id`; empty until `session.created`.
    pub recording_id: String,
    started_at: Option<Instant>,
    /// Audio length, captured when the user stops; `None` while still recording.
    pub last_recording_duration_ms: Option<u64>,
    pub transcript_length: usize,
}

impl RecordingTracking {
    pub fn start(&mut self) {
        *self = Self {
            started_at: Some(Instant::now()),
            ..Self::default()
        };
    }

    /// Milliseconds since the recording began, spanning capture and transcription.
    pub fn elapsed_ms(&self) -> u64 {
        self.started_at.map_or(0, |at| {
            at.elapsed().as_millis().min(u128::from(u64::MAX)) as u64
        })
    }

    pub fn mark_stopped(&mut self) {
        self.last_recording_duration_ms = Some(self.elapsed_ms());
    }

    pub fn add_transcript(&mut self, text: &str) {
        self.transcript_length += text.chars().count();
    }

    /// `vibe.audio.transcription.start` properties.
    pub fn start_properties(&self) -> BTreeMap<String, Value> {
        bmap(json!({"recording_id": self.recording_id}))
    }

    /// `vibe.audio.transcription.cancel_recording` properties.
    pub fn cancel_properties(&self) -> BTreeMap<String, Value> {
        bmap(json!({
            "recording_id": self.recording_id,
            "recording_duration_ms": self.elapsed_ms(),
        }))
    }

    /// `vibe.audio.transcription.done` properties.
    pub fn done_properties(&self) -> BTreeMap<String, Value> {
        let transcription_duration_ms = self.elapsed_ms();
        bmap(json!({
            "recording_id": self.recording_id,
            "transcript_length": self.transcript_length,
            "transcription_duration_ms": transcription_duration_ms,
            "recording_duration_ms": self
                .last_recording_duration_ms
                .unwrap_or(transcription_duration_ms),
        }))
    }

    /// `vibe.audio.transcription.error` properties.
    pub fn error_properties(&self, error_message: &str) -> BTreeMap<String, Value> {
        bmap(json!({
            "recording_id": self.recording_id,
            "error_message": error_message,
            "transcription_duration_ms": self.elapsed_ms(),
            "recording_duration_ms": self.last_recording_duration_ms,
        }))
    }
}
