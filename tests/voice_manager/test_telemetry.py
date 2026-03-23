from __future__ import annotations

from vibe.cli.voice_manager.telemetry import TranscriptionTrackingState


class TestTranscriptionTrackingState:
    def test_reset_clears_accumulated_state(self) -> None:
        state = TranscriptionTrackingState()
        state.set_recording_id("req-1")
        state.record_text("hello")
        state.set_recording_duration(5.0)

        state.reset()
        assert state.recording_id == ""
        assert state.accumulated_transcript_length == 0
        assert state.last_recording_duration_ms is None

    def test_set_recording_id(self) -> None:
        state = TranscriptionTrackingState()
        state.set_recording_id("req-abc")
        assert state.recording_id == "req-abc"

    def test_record_text_accumulates_length(self) -> None:
        state = TranscriptionTrackingState()
        state.reset()
        state.record_text("hello ")
        state.record_text("world")
        assert state.accumulated_transcript_length == 11

    def test_elapsed_ms_returns_positive_value(self) -> None:
        state = TranscriptionTrackingState()
        state.reset()
        assert state.elapsed_ms() >= 0

    def test_set_recording_duration_converts_seconds_to_ms(self) -> None:
        state = TranscriptionTrackingState()
        state.set_recording_duration(2.5)
        assert state.last_recording_duration_ms == 2500.0

    def test_default_state(self) -> None:
        state = TranscriptionTrackingState()
        assert state.recording_id == ""
        assert state.accumulated_transcript_length == 0
        assert state.last_recording_duration_ms is None
