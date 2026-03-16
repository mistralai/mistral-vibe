from __future__ import annotations

import asyncio
import io
import struct
import time
from unittest.mock import MagicMock, patch
import wave

import pytest

try:
    import sounddevice as sd
except OSError:
    pytest.skip("PortAudio library not available", allow_module_level=True)

from vibe.core.audio_recorder.audio_recorder import AudioRecorder
from vibe.core.audio_recorder.audio_recorder_port import (
    AlreadyRecordingError,
    AudioBackendUnavailableError,
    AudioRecording,
    IncompatibleSampleRateError,
    NoAudioInputDeviceError,
    RecordingMode,
)


def _make_pcm_frames(value: int, n_samples: int = 1024) -> bytes:
    """Create raw PCM int16 bytes with all samples set to `value`."""
    return struct.pack(f"<{n_samples}h", *([value] * n_samples))


def _get_callback(mock_stream_cls: MagicMock):
    """Extract the callback kwarg passed to the mocked RawInputStream."""
    return mock_stream_cls.call_args.kwargs["callback"]


class TestAudioRecorderInitialState:
    def test_not_recording(self) -> None:
        recorder = AudioRecorder()
        assert recorder.is_recording is False

    def test_peak_is_zero(self) -> None:
        recorder = AudioRecorder()
        assert recorder.peak == 0.0


class TestBufferMode:
    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_start_sets_recording_state(self, mock_stream_cls: MagicMock) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER)
        assert recorder.is_recording is True
        mock_stream_cls.return_value.start.assert_called_once()

    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_start_when_already_recording_raises(
        self, mock_stream_cls: MagicMock
    ) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER)

        with pytest.raises(AlreadyRecordingError):
            recorder.start(RecordingMode.BUFFER)

    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_stop_returns_valid_wav(self, mock_stream_cls: MagicMock) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER)

        callback = _get_callback(mock_stream_cls)
        pcm_data = _make_pcm_frames(5000)
        callback(pcm_data, 1024, {}, sd.CallbackFlags())

        result = recorder.stop()

        assert recorder.is_recording is False
        assert len(result.data) > 0
        assert result.data[:4] == b"RIFF"

        with wave.open(io.BytesIO(result.data), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 48_000
            assert wf.getnframes() == 1024

    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_stop_returns_positive_duration(self, mock_stream_cls: MagicMock) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER)
        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(100), 1024, {}, sd.CallbackFlags())

        result = recorder.stop()
        assert result.duration > 0.0

    def test_stop_when_not_recording_returns_empty(self) -> None:
        recorder = AudioRecorder()
        result = recorder.stop()
        assert result.data == b""
        assert result.duration == 0.0

    @pytest.mark.asyncio
    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    async def test_buffer_mode_audio_stream_yields_nothing(
        self, mock_stream_cls: MagicMock
    ) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER)

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(5000), 1024, {}, sd.CallbackFlags())

        collected: list[bytes] = []
        async for chunk in recorder.audio_stream():
            collected.append(chunk)

        assert collected == []

    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_can_record_multiple_times(self, mock_stream_cls: MagicMock) -> None:
        recorder = AudioRecorder()

        recorder.start(RecordingMode.BUFFER)
        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(5000), 1024, {}, sd.CallbackFlags())
        result1 = recorder.stop()
        assert len(result1.data) > 0

        recorder.start(RecordingMode.BUFFER)
        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(3000), 1024, {}, sd.CallbackFlags())
        result2 = recorder.stop()
        assert len(result2.data) > 0

        assert result1.data[:4] == b"RIFF"
        assert result2.data[:4] == b"RIFF"


class TestStreamMode:
    @pytest.mark.asyncio
    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    async def test_audio_stream_yields_chunks(self, mock_stream_cls: MagicMock) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.STREAM)

        callback = _get_callback(mock_stream_cls)
        chunk1 = _make_pcm_frames(1000, n_samples=512)
        chunk2 = _make_pcm_frames(2000, n_samples=512)

        collected: list[bytes] = []

        async def consume() -> None:
            async for chunk in recorder.audio_stream():
                collected.append(chunk)

        task = asyncio.create_task(consume())

        callback(chunk1, 512, {}, sd.CallbackFlags())
        callback(chunk2, 512, {}, sd.CallbackFlags())
        await asyncio.sleep(0.05)

        recorder.stop()
        await task

        assert len(collected) == 2
        assert collected[0] == chunk1
        assert collected[1] == chunk2

    @pytest.mark.asyncio
    async def test_audio_stream_without_start_returns_nothing(self) -> None:
        recorder = AudioRecorder()
        collected: list[bytes] = []
        async for chunk in recorder.audio_stream():
            collected.append(chunk)
        assert collected == []

    @pytest.mark.asyncio
    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    async def test_stream_audio_does_not_leak_into_buffer_recording(
        self, mock_stream_cls: MagicMock
    ) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.STREAM)

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(1000), 1024, {}, sd.CallbackFlags())

        async def consume() -> None:
            async for _ in recorder.audio_stream():
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)

        recorder.stop()
        await task

        recorder.start(RecordingMode.BUFFER)
        result = recorder.stop()

        with wave.open(io.BytesIO(result.data), "rb") as wf:
            assert wf.getnframes() == 0

    @pytest.mark.asyncio
    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    async def test_stop_from_event_loop_does_not_block(
        self, mock_stream_cls: MagicMock
    ) -> None:
        """stop() called from the event loop thread must not block waiting for drain."""
        recorder = AudioRecorder()
        recorder.start(RecordingMode.STREAM)

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(1000), 1024, {}, sd.CallbackFlags())

        collected: list[bytes] = []

        async def consume() -> None:
            async for chunk in recorder.audio_stream():
                collected.append(chunk)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)

        start = time.monotonic()
        result = recorder.stop()
        elapsed = time.monotonic() - start

        await task

        assert elapsed < 1.0
        assert result.data == b""
        assert result.duration > 0.0
        assert len(collected) == 1

    @pytest.mark.asyncio
    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    async def test_stop_returns_empty_data_in_stream_mode(
        self, mock_stream_cls: MagicMock
    ) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.STREAM)

        async def consume() -> None:
            async for _ in recorder.audio_stream():
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        result = recorder.stop()
        await task

        assert result.data == b""
        assert result.duration > 0.0

    @pytest.mark.asyncio
    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    async def test_stop_without_drain_returns_promptly(
        self, mock_stream_cls: MagicMock
    ) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.STREAM)

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(1000), 1024, {}, sd.CallbackFlags())

        start = time.monotonic()
        result = recorder.stop(wait_for_queue_drained=False)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0
        assert result.data == b""
        assert result.duration > 0.0
        assert recorder.is_recording is False


class TestCancel:
    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_cancel_discards_audio(self, mock_stream_cls: MagicMock) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER)

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(5000), 1024, {}, sd.CallbackFlags())

        recorder.cancel()
        assert recorder.is_recording is False
        mock_stream_cls.return_value.stop.assert_called_once()
        mock_stream_cls.return_value.close.assert_called_once()

    def test_cancel_when_not_recording_is_noop(self) -> None:
        recorder = AudioRecorder()
        recorder.cancel()
        assert recorder.is_recording is False


class TestMaxDuration:
    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_auto_stops_after_max_duration(self, mock_stream_cls: MagicMock) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER, max_duration=0.1)

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(5000), 1024, {}, sd.CallbackFlags())

        time.sleep(0.3)

        assert recorder.is_recording is False

    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_on_expire_receives_audio(self, mock_stream_cls: MagicMock) -> None:
        """on_expire callback receives the WAV data when the timer fires."""
        received: list[AudioRecording] = []
        recorder = AudioRecorder()
        recorder.start(
            RecordingMode.BUFFER, max_duration=0.1, on_expire=received.append
        )

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(5000), 1024, {}, sd.CallbackFlags())

        time.sleep(0.3)
        assert recorder.is_recording is False
        assert len(received) == 1
        assert received[0].data[:4] == b"RIFF"

    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_manual_stop_prevents_on_expire(self, mock_stream_cls: MagicMock) -> None:
        expired: list[AudioRecording] = []
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER, max_duration=0.2, on_expire=expired.append)

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(5000), 1024, {}, sd.CallbackFlags())

        result = recorder.stop()
        assert recorder.is_recording is False
        assert result.data[:4] == b"RIFF"

        time.sleep(0.35)
        assert expired == []
        assert recorder.is_recording is False


class TestPeak:
    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_peak_updates_from_callback(self, mock_stream_cls: MagicMock) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER)

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(16_384), 1024, {}, sd.CallbackFlags())

        assert recorder.peak == pytest.approx(16_384 / 32_768, abs=0.01)

    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_peak_clamps_to_one(self, mock_stream_cls: MagicMock) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER)

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(32_767), 1024, {}, sd.CallbackFlags())

        assert recorder.peak <= 1.0

    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_silent_audio_has_zero_peak(self, mock_stream_cls: MagicMock) -> None:
        recorder = AudioRecorder()
        recorder.start(RecordingMode.BUFFER)

        callback = _get_callback(mock_stream_cls)
        callback(_make_pcm_frames(0), 1024, {}, sd.CallbackFlags())

        assert recorder.peak == 0.0


class TestGuardAudioInput:
    def test_guard_returns_sample_rate_when_compatible(self) -> None:
        with (
            patch(
                "vibe.core.audio_recorder.audio_recorder.sd.query_devices"
            ) as mock_query,
            patch("vibe.core.audio_recorder.audio_recorder.sd.check_input_settings"),
        ):
            mock_query.return_value = {"default_samplerate": 48000.0}
            result = AudioRecorder._guard_audio_input(48000, 1)
            assert result == 48000

    def test_guard_raises_when_no_input_device(self) -> None:
        with patch(
            "vibe.core.audio_recorder.audio_recorder.sd.query_devices",
            side_effect=sd.PortAudioError(-1),
        ):
            with pytest.raises(NoAudioInputDeviceError):
                AudioRecorder._guard_audio_input(48000, 1)

    def test_guard_raises_with_fallback_when_rate_incompatible(self) -> None:
        with (
            patch(
                "vibe.core.audio_recorder.audio_recorder.sd.query_devices"
            ) as mock_query,
            patch(
                "vibe.core.audio_recorder.audio_recorder.sd.check_input_settings",
                side_effect=sd.PortAudioError(-1),
            ),
        ):
            mock_query.return_value = {"default_samplerate": 16000.0}
            with pytest.raises(IncompatibleSampleRateError) as exc_info:
                AudioRecorder._guard_audio_input(48000, 1)
            assert exc_info.value.fallback_sample_rate == 16000

    def test_start_raises_when_no_sounddevice(self) -> None:
        with patch("vibe.core.audio_recorder.audio_recorder.sd", None):
            recorder = AudioRecorder()
            with pytest.raises(AudioBackendUnavailableError):
                recorder.start(RecordingMode.BUFFER)

    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_start_raises_when_no_device(self, mock_stream_cls: MagicMock) -> None:
        with patch(
            "vibe.core.audio_recorder.audio_recorder.sd.query_devices",
            side_effect=sd.PortAudioError(-1),
        ):
            recorder = AudioRecorder()
            with pytest.raises(NoAudioInputDeviceError):
                recorder.start(RecordingMode.BUFFER)
            assert recorder.is_recording is False
            mock_stream_cls.assert_not_called()

    @patch("vibe.core.audio_recorder.audio_recorder.sd.RawInputStream")
    def test_start_retries_with_fallback_sample_rate(
        self, mock_stream_cls: MagicMock
    ) -> None:
        with (
            patch(
                "vibe.core.audio_recorder.audio_recorder.sd.query_devices"
            ) as mock_query,
            patch(
                "vibe.core.audio_recorder.audio_recorder.sd.check_input_settings"
            ) as mock_check,
        ):
            mock_query.return_value = {"default_samplerate": 16000.0}
            mock_check.side_effect = sd.PortAudioError(-1)

            recorder = AudioRecorder()
            recorder.start(RecordingMode.BUFFER)

            assert recorder.is_recording is True
            assert mock_stream_cls.call_args.kwargs["samplerate"] == 16000
