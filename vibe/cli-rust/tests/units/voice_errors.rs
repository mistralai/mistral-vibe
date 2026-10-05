//! Voice silence diagnostics, platform hints, and transcription failures.

use std::time::Duration;

use futures::stream;
use serde_json::json;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::{Error, Message};
use vibe_rs::app::{App, ToastSeverity};
use vibe_rs::voice::signal::RecordingSignal;
use vibe_rs::voice::transcribe::read_events;
use vibe_rs::voice::{TranscribeState, VoiceEvent};

const NO_AUDIO: &str = "No audio detected from microphone — check your terminal has mic access.";

#[test]
fn silent_recordings_show_platform_specific_mic_access_instructions() {
    for (platform, hint) in [
        (
            "macos",
            " Grant access in System Settings → Privacy & Security → Microphone.",
        ),
        (
            "windows",
            " Grant access in Settings → Privacy & security → Microphone.",
        ),
        ("linux", ""),
    ] {
        let signal = RecordingSignal::default();
        signal.observe(0.0);
        signal.stop(Duration::from_millis(500));

        let Some(VoiceEvent::Error(message)) = signal.completion(false, platform) else {
            panic!("a silent microphone must be reported as an error");
        };
        assert_eq!(message, format!("{NO_AUDIO}{hint}"));
    }
}

#[test]
fn short_recordings_do_not_claim_microphone_access_is_denied() {
    let signal = RecordingSignal::default();
    signal.stop(Duration::from_millis(499));

    assert!(
        matches!(signal.completion(false, "macos"), Some(VoiceEvent::Notice(text)) if text == "No speech detected")
    );
}

#[test]
fn recordings_that_have_not_been_stopped_do_not_claim_microphone_access_is_denied() {
    assert!(matches!(
        RecordingSignal::default().completion(false, "macos"),
        Some(VoiceEvent::Notice(_))
    ));
}

#[test]
fn a_signal_anywhere_in_the_recording_prevents_the_microphone_warning() {
    let signal = RecordingSignal::default();
    signal.observe(0.002);
    signal.observe(0.0);
    signal.stop(Duration::from_secs(5));

    assert!(
        matches!(signal.completion(false, "macos"), Some(VoiceEvent::Notice(text)) if text == "No speech detected")
    );
}

#[test]
fn the_silence_floor_is_not_a_signal() {
    let signal = RecordingSignal::default();
    signal.observe(0.001);
    signal.stop(Duration::from_secs(5));

    assert!(matches!(
        signal.completion(false, "linux"),
        Some(VoiceEvent::Error(_))
    ));
}

#[test]
fn a_transcript_suppresses_empty_recording_diagnostics() {
    let signal = RecordingSignal::default();
    signal.stop(Duration::from_secs(5));

    assert!(signal.completion(true, "macos").is_none());
}

#[test]
fn transcription_errors_have_the_python_prefix_and_settle_recording() {
    let mut app = App::default();
    app.voice.transcribe_state = TranscribeState::Flushing;

    app.apply_voice_event(VoiceEvent::Error(NO_AUDIO.to_owned()));

    assert!(!app.recording_active());
    let toast = app.overlays.toasts.back().unwrap();
    assert!(matches!(toast.severity, ToastSeverity::Error));
    assert_eq!(
        toast.text,
        format!("Voice transcription failed: {NO_AUDIO}")
    );
}

#[tokio::test]
async fn empty_text_deltas_are_not_a_transcript() {
    let (tx, mut rx) = mpsc::channel(4);
    let mut messages = stream::iter([
        Ok(Message::Text(
            json!({"type": "transcription.text.delta", "text": ""}).to_string(),
        )),
        Ok(Message::Text(
            json!({"type": "transcription.done"}).to_string(),
        )),
    ]);

    assert!(!read_events(&mut messages, &tx).await.unwrap());
    assert!(matches!(rx.try_recv().unwrap(), VoiceEvent::TextDelta(text) if text.is_empty()));
    assert!(rx.try_recv().is_err());
}

#[tokio::test]
async fn text_is_forwarded_and_marks_a_nonempty_transcript() {
    let (tx, mut rx) = mpsc::channel(4);
    let mut messages = stream::iter([
        Ok(Message::Text(
            json!({"type": "transcription.text.delta", "text": "hello"}).to_string(),
        )),
        Ok(Message::Text(
            json!({"type": "transcription.done"}).to_string(),
        )),
    ]);

    assert!(read_events(&mut messages, &tx).await.unwrap());
    assert!(matches!(rx.try_recv().unwrap(), VoiceEvent::TextDelta(text) if text == "hello"));
}

#[tokio::test]
async fn service_errors_are_not_reported_as_no_speech() {
    let (tx, mut rx) = mpsc::channel(4);
    let mut messages = stream::iter([Ok(Message::Text(
        json!({
            "type": "error", "error": {"message": "Transcription quota exceeded"},
        })
        .to_string(),
    ))]);

    assert_eq!(
        read_events(&mut messages, &tx).await.unwrap_err(),
        "Transcription quota exceeded"
    );
    assert!(rx.try_recv().is_err());
}

#[tokio::test]
async fn a_flush_without_audio_is_classified_by_the_recording_signal() {
    let (tx, _) = mpsc::channel(4);
    let mut messages = stream::iter([Ok(Message::Text(
        json!({
            "type": "error", "error": {"message": "Cannot flush before sending any audio bytes"},
        })
        .to_string(),
    ))]);

    let got_text = read_events(&mut messages, &tx).await.unwrap();
    let signal = RecordingSignal::default();
    signal.stop(Duration::from_secs(1));
    assert!(matches!(
        signal.completion(got_text, "macos"),
        Some(VoiceEvent::Error(_))
    ));
}

#[tokio::test]
async fn websocket_read_errors_are_not_silently_discarded() {
    let (tx, mut rx) = mpsc::channel(4);
    let mut messages = stream::iter([Err(Error::ConnectionClosed)]);

    assert!(read_events(&mut messages, &tx)
        .await
        .unwrap_err()
        .starts_with("Transcription connection failed:"));
    assert!(rx.try_recv().is_err());
}
