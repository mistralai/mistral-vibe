from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_audio_recorder import FakeAudioRecorder
from tests.stubs.fake_transcribe_client import FakeTranscribeClient
from vibe.cli.voice_manager.voice_manager import VoiceManager
from vibe.cli.voice_manager.voice_manager_port import (
    RecordingStartError,
    TranscribeState,
    VoiceManagerListener,
    VoiceToggleResult,
)
from vibe.core.audio_recorder.audio_recorder_port import (
    AudioBackendUnavailableError,
    NoAudioInputDeviceError,
)
from vibe.core.config import VibeConfig
from vibe.core.transcribe.transcribe_client_port import (
    TranscribeDone,
    TranscribeError,
    TranscribeSessionCreated,
    TranscribeTextDelta,
)


class StateListener(VoiceManagerListener):
    def __init__(self) -> None:
        self.state_changes: list[TranscribeState] = []
        self.voice_mode_changes: list[bool] = []
        self.transcribed_texts: list[str] = []

    def on_transcribe_state_change(self, state: TranscribeState) -> None:
        self.state_changes.append(state)

    def on_voice_mode_change(self, enabled: bool) -> None:
        self.voice_mode_changes.append(enabled)

    def on_transcribe_text(self, text: str) -> None:
        self.transcribed_texts.append(text)


def _make_manager(
    *,
    voice_mode_enabled: bool = True,
    transcribe_client: FakeTranscribeClient | None = None,
    telemetry_client: MagicMock | None = None,
) -> tuple[VoiceManager, FakeAudioRecorder, FakeTranscribeClient]:
    recorder = FakeAudioRecorder()
    client = transcribe_client or FakeTranscribeClient()
    config = build_test_vibe_config(voice_mode_enabled=voice_mode_enabled)
    manager = VoiceManager(
        config_getter=lambda: config,
        audio_recorder=recorder,
        transcribe_client=client,
        telemetry_client=telemetry_client,
    )
    return manager, recorder, client


class TestStartRecording:
    @pytest.mark.asyncio
    async def test_start_sets_state_to_recording(self) -> None:
        manager, _, _ = _make_manager()
        manager.start_recording()
        assert manager.transcribe_state == TranscribeState.RECORDING

    @pytest.mark.asyncio
    async def test_start_starts_audio_recorder_in_stream_mode(self) -> None:
        manager, recorder, _ = _make_manager()
        manager.start_recording()
        assert recorder.is_recording

    @pytest.mark.asyncio
    async def test_start_noop_when_not_idle(self) -> None:
        manager, recorder, _ = _make_manager()
        manager.start_recording()
        recorder.set_peak(0.5)
        manager.start_recording()
        assert recorder.peak == 0.5
        assert manager.transcribe_state == TranscribeState.RECORDING

    @pytest.mark.asyncio
    async def test_start_raises_when_no_audio_input(self) -> None:
        def raise_no_input(*a, **kw):
            raise NoAudioInputDeviceError("no device")

        manager, recorder, _ = _make_manager()
        recorder.start = raise_no_input
        with pytest.raises(RecordingStartError, match="No audio input device found"):
            manager.start_recording()
        assert manager.transcribe_state == TranscribeState.IDLE

    @pytest.mark.asyncio
    async def test_start_raises_when_no_backend(self) -> None:
        def raise_no_backend(*a, **kw):
            raise AudioBackendUnavailableError("no sd")

        manager, recorder, _ = _make_manager()
        recorder.start = raise_no_backend
        with pytest.raises(RecordingStartError, match="Audio backend is unavailable"):
            manager.start_recording()
        assert manager.transcribe_state == TranscribeState.IDLE

    @pytest.mark.asyncio
    async def test_start_raises_when_no_transcribe_client(self) -> None:
        recorder = FakeAudioRecorder()
        config = build_test_vibe_config(voice_mode_enabled=True)
        manager = VoiceManager(
            config_getter=lambda: config,
            audio_recorder=recorder,
            transcribe_client=None,
        )
        with pytest.raises(
            RecordingStartError, match="Transcribe client is not available"
        ):
            manager.start_recording()
        assert manager.transcribe_state == TranscribeState.IDLE
        assert not recorder.is_recording


class TestStopRecording:
    @pytest.mark.asyncio
    async def test_stop_transitions_through_flushing_to_idle(self) -> None:
        manager, _, _ = _make_manager()
        listener = StateListener()
        manager.add_listener(listener)

        manager.start_recording()
        await manager.stop_recording()

        assert listener.state_changes == [
            TranscribeState.RECORDING,
            TranscribeState.FLUSHING,
            TranscribeState.IDLE,
        ]

    @pytest.mark.asyncio
    async def test_stop_noop_when_not_recording(self) -> None:
        manager, _, _ = _make_manager()
        listener = StateListener()
        manager.add_listener(listener)
        await manager.stop_recording()
        assert listener.state_changes == []

    @pytest.mark.asyncio
    async def test_stop_recovers_after_transcription_timeout(self) -> None:
        import asyncio

        class HangingTranscribeClient:
            def __init__(self, provider=None, model=None) -> None:
                pass

            async def transcribe(self, audio_stream):
                await asyncio.Event().wait()
                return
                yield  # makes this an async generator

        recorder = FakeAudioRecorder()
        config = build_test_vibe_config(voice_mode_enabled=True)
        manager = VoiceManager(
            config_getter=lambda: config,
            audio_recorder=recorder,
            transcribe_client=HangingTranscribeClient(),
        )
        manager.start_recording()

        with patch(
            "vibe.cli.voice_manager.voice_manager.TRANSCRIPTION_DRAIN_TIMEOUT", 0.01
        ):
            await manager.stop_recording()

        assert manager.transcribe_state == TranscribeState.IDLE

    @pytest.mark.asyncio
    async def test_stop_recovers_when_no_audio_was_sent(self) -> None:
        client = FakeTranscribeClient(
            events=[
                TranscribeSessionCreated(request_id="test-req-id"),
                TranscribeError(
                    message="Cannot flush audio before sending any audio bytes"
                ),
            ]
        )
        manager, recorder, _ = _make_manager(transcribe_client=client)
        listener = StateListener()
        manager.add_listener(listener)

        manager.start_recording()
        await manager.stop_recording()

        assert manager.transcribe_state == TranscribeState.IDLE
        assert not recorder.is_recording


class TestCancelRecording:
    @pytest.mark.asyncio
    async def test_cancel_from_recording(self) -> None:
        manager, recorder, _ = _make_manager()
        manager.start_recording()
        manager.cancel_recording()
        assert manager.transcribe_state == TranscribeState.IDLE
        assert not recorder.is_recording

    @pytest.mark.asyncio
    async def test_cancel_then_start_not_corrupted_by_stale_finally(self) -> None:
        import asyncio

        class HangingTranscribeClient:
            def __init__(self, provider=None, model=None) -> None:
                pass

            async def transcribe(self, audio_stream):
                await asyncio.Event().wait()
                return
                yield

        recorder = FakeAudioRecorder()
        config = build_test_vibe_config(voice_mode_enabled=True)
        manager = VoiceManager(
            config_getter=lambda: config,
            audio_recorder=recorder,
            transcribe_client=HangingTranscribeClient(),
        )

        manager.start_recording()
        manager.cancel_recording()
        manager.start_recording()

        await asyncio.sleep(0)

        assert manager.transcribe_state == TranscribeState.RECORDING

    def test_cancel_noop_when_idle(self) -> None:
        manager, _, _ = _make_manager()
        listener = StateListener()
        manager.add_listener(listener)
        manager.cancel_recording()
        assert listener.state_changes == []


class TestToggleVoiceMode:
    @patch.object(VibeConfig, "save_updates")
    def test_toggle_enables(self, _mock_save) -> None:
        manager, _, _ = _make_manager(voice_mode_enabled=False)
        result = manager.toggle_voice_mode()
        assert result == VoiceToggleResult(enabled=True)

    @patch.object(VibeConfig, "save_updates")
    def test_toggle_disables(self, _mock_save) -> None:
        manager, _, _ = _make_manager(voice_mode_enabled=True)
        result = manager.toggle_voice_mode()
        assert result == VoiceToggleResult(enabled=False)

    @pytest.mark.asyncio
    @patch.object(VibeConfig, "save_updates")
    async def test_toggle_disable_cancels_active_recording(self, _mock_save) -> None:
        manager, _, _ = _make_manager(voice_mode_enabled=True)
        manager.start_recording()
        manager.toggle_voice_mode()
        assert manager.transcribe_state == TranscribeState.IDLE


class TestListeners:
    @pytest.mark.asyncio
    async def test_listener_notified_on_state_change(self) -> None:
        manager, _, _ = _make_manager()
        listener = StateListener()
        manager.add_listener(listener)
        manager.start_recording()
        assert listener.state_changes == [TranscribeState.RECORDING]

    @patch.object(VibeConfig, "save_updates")
    def test_listener_notified_on_voice_mode_change(self, _mock_save) -> None:
        manager, _, _ = _make_manager(voice_mode_enabled=False)
        listener = StateListener()
        manager.add_listener(listener)
        manager.toggle_voice_mode()
        assert listener.voice_mode_changes == [True]

    @pytest.mark.asyncio
    async def test_remove_listener(self) -> None:
        manager, _, _ = _make_manager()
        listener = StateListener()
        manager.add_listener(listener)
        manager.remove_listener(listener)
        manager.start_recording()
        assert listener.state_changes == []

    def test_remove_nonexistent_listener_no_error(self) -> None:
        manager, _, _ = _make_manager()
        listener = StateListener()
        manager.remove_listener(listener)


class TestPeak:
    def test_peak_delegates_to_audio_recorder(self) -> None:
        manager, recorder, _ = _make_manager()
        recorder.set_peak(0.42)
        assert manager.peak == 0.42


class TestTranscription:
    @pytest.mark.asyncio
    async def test_text_deltas_notify_listeners(self) -> None:
        client = FakeTranscribeClient(
            events=[
                TranscribeSessionCreated(request_id="test-req-id"),
                TranscribeTextDelta(text="hello "),
                TranscribeTextDelta(text="world"),
                TranscribeDone(),
            ]
        )
        manager, _, _ = _make_manager(transcribe_client=client)
        listener = StateListener()
        manager.add_listener(listener)

        manager.start_recording()
        await manager.stop_recording()

        assert listener.transcribed_texts == ["hello ", "world"]

    @pytest.mark.asyncio
    async def test_transcription_error_does_not_crash(self) -> None:
        client = FakeTranscribeClient(
            events=[
                TranscribeSessionCreated(request_id="test-req-id"),
                TranscribeTextDelta(text="partial"),
                TranscribeError(message="something broke"),
                TranscribeDone(),
            ]
        )
        manager, _, _ = _make_manager(transcribe_client=client)
        listener = StateListener()
        manager.add_listener(listener)

        manager.start_recording()
        await manager.stop_recording()

        assert "partial" in listener.transcribed_texts

    @pytest.mark.asyncio
    async def test_cancel_during_transcription(self) -> None:
        client = FakeTranscribeClient(
            events=[
                TranscribeSessionCreated(request_id="test-req-id"),
                TranscribeTextDelta(text="hello"),
            ]
        )
        manager, _, _ = _make_manager(transcribe_client=client)
        listener = StateListener()
        manager.add_listener(listener)

        manager.start_recording()
        manager.cancel_recording()

        assert manager.transcribe_state == TranscribeState.IDLE

    @pytest.mark.asyncio
    async def test_session_created_is_silent(self) -> None:
        client = FakeTranscribeClient(
            events=[
                TranscribeSessionCreated(request_id="test-req-id"),
                TranscribeDone(),
            ]
        )
        manager, _, _ = _make_manager(transcribe_client=client)
        listener = StateListener()
        manager.add_listener(listener)

        manager.start_recording()
        await manager.stop_recording()

        assert listener.transcribed_texts == []

    @pytest.mark.asyncio
    async def test_transcription_exception_stops_recorder(self) -> None:
        import asyncio

        class CrashingTranscribeClient:
            def __init__(self, provider=None, model=None) -> None:
                pass

            async def transcribe(self, audio_stream):
                raise RuntimeError("network error")
                yield  # makes this an async generator

        recorder = FakeAudioRecorder()
        config = build_test_vibe_config(voice_mode_enabled=True)
        manager = VoiceManager(
            config_getter=lambda: config,
            audio_recorder=recorder,
            transcribe_client=CrashingTranscribeClient(),
        )
        manager.start_recording()
        assert recorder.is_recording

        await asyncio.sleep(0)

        assert manager.transcribe_state == TranscribeState.IDLE
        assert not recorder.is_recording


def _find_telemetry_calls(
    mock: MagicMock, event_name: str
) -> list[dict[str, str | int | float | None]]:
    """Return the properties dicts for all calls matching a given event name."""
    results: list[dict[str, str | int | float | None]] = []
    for call in mock.send_telemetry_event.call_args_list:
        if call[0][0] == event_name:
            results.append(call[0][1])
    return results


class TestTelemetryTracking:
    @pytest.mark.asyncio
    async def test_start_sends_transcription_start_event(self) -> None:
        client = FakeTranscribeClient(
            events=[TranscribeSessionCreated(request_id="req-123"), TranscribeDone()]
        )
        mock_telemetry = MagicMock()
        manager, _, _ = _make_manager(
            transcribe_client=client, telemetry_client=mock_telemetry
        )
        manager.start_recording()
        await manager.stop_recording()

        calls = _find_telemetry_calls(mock_telemetry, "vibe.audio.transcription.start")
        assert len(calls) == 1
        assert calls[0]["recording_id"] == "req-123"

    @pytest.mark.asyncio
    async def test_cancel_sends_cancel_event(self) -> None:
        mock_telemetry = MagicMock()
        manager, _, _ = _make_manager(telemetry_client=mock_telemetry)
        manager.start_recording()
        manager.cancel_recording()

        calls = _find_telemetry_calls(
            mock_telemetry, "vibe.audio.transcription.cancel_recording"
        )
        assert len(calls) == 1
        recording_duration_ms = calls[0]["recording_duration_ms"]
        assert isinstance(recording_duration_ms, (int, float))
        assert recording_duration_ms >= 0

    @pytest.mark.asyncio
    async def test_done_sends_done_event(self) -> None:
        client = FakeTranscribeClient(
            events=[
                TranscribeSessionCreated(request_id="test-req-id"),
                TranscribeTextDelta(text="hello "),
                TranscribeTextDelta(text="world"),
                TranscribeDone(),
            ]
        )
        mock_telemetry = MagicMock()
        manager, _, _ = _make_manager(
            transcribe_client=client, telemetry_client=mock_telemetry
        )

        manager.start_recording()
        await manager.stop_recording()

        calls = _find_telemetry_calls(mock_telemetry, "vibe.audio.transcription.done")
        assert len(calls) == 1
        assert calls[0]["recording_id"] == "test-req-id"
        assert calls[0]["transcript_length"] == len("hello ") + len("world")
        transcription_duration_ms = calls[0]["transcription_duration_ms"]
        assert isinstance(transcription_duration_ms, (int, float))
        assert transcription_duration_ms >= 0
        recording_duration_ms = calls[0]["recording_duration_ms"]
        assert isinstance(recording_duration_ms, (int, float))
        assert recording_duration_ms >= 0

    @pytest.mark.asyncio
    async def test_error_sends_error_event(self) -> None:
        import asyncio

        class CrashingTranscribeClient:
            def __init__(self, provider=None, model=None) -> None:
                pass

            async def transcribe(self, audio_stream):
                raise RuntimeError("network error")
                yield

        recorder = FakeAudioRecorder()
        config = build_test_vibe_config(voice_mode_enabled=True)
        mock_telemetry = MagicMock()
        manager = VoiceManager(
            config_getter=lambda: config,
            audio_recorder=recorder,
            transcribe_client=CrashingTranscribeClient(),
            telemetry_client=mock_telemetry,
        )

        manager.start_recording()
        await asyncio.sleep(0)

        calls = _find_telemetry_calls(mock_telemetry, "vibe.audio.transcription.error")
        assert len(calls) == 1
        error_message = calls[0]["error_message"]
        assert isinstance(error_message, str)
        assert "network error" in error_message
        transcription_duration_ms = calls[0]["transcription_duration_ms"]
        assert isinstance(transcription_duration_ms, (int, float))
        assert transcription_duration_ms >= 0

    @pytest.mark.asyncio
    async def test_no_telemetry_when_client_is_none(self) -> None:
        manager, _, _ = _make_manager()  # no telemetry_client
        manager.start_recording()
        manager.cancel_recording()
        # No error raised — tracking is silently skipped

    @pytest.mark.asyncio
    async def test_each_recording_uses_session_request_id(self) -> None:
        client = FakeTranscribeClient(
            events=[TranscribeSessionCreated(request_id="req-first"), TranscribeDone()]
        )
        mock_telemetry = MagicMock()
        manager, _, _ = _make_manager(
            transcribe_client=client, telemetry_client=mock_telemetry
        )

        manager.start_recording()
        await manager.stop_recording()

        client.set_events([
            TranscribeSessionCreated(request_id="req-second"),
            TranscribeDone(),
        ])

        manager.start_recording()
        await manager.stop_recording()

        calls = _find_telemetry_calls(mock_telemetry, "vibe.audio.transcription.start")
        assert len(calls) == 2
        assert calls[0]["recording_id"] == "req-first"
        assert calls[1]["recording_id"] == "req-second"

    @pytest.mark.asyncio
    async def test_timeout_sends_error_event(self) -> None:
        import asyncio

        class HangingTranscribeClient:
            def __init__(self, provider=None, model=None) -> None:
                pass

            async def transcribe(self, audio_stream):
                await asyncio.Event().wait()
                return
                yield

        recorder = FakeAudioRecorder()
        config = build_test_vibe_config(voice_mode_enabled=True)
        mock_telemetry = MagicMock()
        manager = VoiceManager(
            config_getter=lambda: config,
            audio_recorder=recorder,
            transcribe_client=HangingTranscribeClient(),
            telemetry_client=mock_telemetry,
        )
        manager.start_recording()

        with patch(
            "vibe.cli.voice_manager.voice_manager.TRANSCRIPTION_DRAIN_TIMEOUT", 0.01
        ):
            await manager.stop_recording()

        calls = _find_telemetry_calls(mock_telemetry, "vibe.audio.transcription.error")
        assert len(calls) == 1
        error_message = calls[0]["error_message"]
        assert isinstance(error_message, str)
        assert "timed out" in error_message.lower()
