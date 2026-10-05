//! Per-recording analytics bookkeeping (Python `VoiceManager._tracking`).

use vibe_rs::voice::tracking::RecordingTracking;

#[test]
fn start_resets_and_marks_bookkeeping() {
    let mut tracking = RecordingTracking::default();
    tracking.add_transcript("leftover");
    tracking.recording_id = "previous-request".into();
    tracking.start();
    assert!(
        tracking.recording_id.is_empty(),
        "the server names each recording"
    );
    assert_eq!(tracking.transcript_length, 0);
    assert_eq!(tracking.last_recording_duration_ms, None);
}

#[test]
fn transcript_length_counts_chars_not_bytes() {
    let mut tracking = RecordingTracking::default();
    tracking.start();
    tracking.add_transcript("hé");
    assert_eq!(tracking.transcript_length, 2);
}

#[test]
fn mark_stopped_freezes_audio_length() {
    let mut tracking = RecordingTracking::default();
    tracking.start();
    tracking.mark_stopped();
    let stopped = tracking.last_recording_duration_ms;
    assert!(stopped.is_some());
    // A later start clears the frozen length for the next recording.
    tracking.start();
    assert_eq!(tracking.last_recording_duration_ms, None);
}
