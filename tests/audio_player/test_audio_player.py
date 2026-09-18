from __future__ import annotations

import io
import struct
from unittest.mock import MagicMock, patch
import wave

import pytest

try:
    import miniaudio as ma
except (OSError, ImportError):
    pytest.skip("miniaudio library not available", allow_module_level=True)

from vibe.cli.audio_player.audio_player import AudioPlayer
from vibe.cli.audio_player.audio_player_port import (
    AlreadyPlayingError,
    AudioBackendUnavailableError,
    AudioFormat,
    NoAudioOutputDeviceError,
    UnsupportedAudioFormatError,
)


@pytest.fixture(autouse=True)
def available_output_device(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_devices = MagicMock()
    mock_devices.return_value.get_playbacks.return_value = [{"name": "test speaker"}]
    monkeypatch.setattr("vibe.cli.audio_player.audio_player.ma.Devices", mock_devices)


def _make_wav_bytes(
    n_frames: int = 1024, sample_rate: int = 48_000, channels: int = 1
) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(
            struct.pack(f"<{n_frames * channels}h", *([1000] * n_frames * channels))
        )
    return buf.getvalue()


def _get_generator(mock_device_cls: MagicMock):
    return mock_device_cls.return_value.start.call_args[0][0]


class TestAudioPlayerInitialState:
    def test_not_playing(self) -> None:
        player = AudioPlayer()
        assert player.is_playing is False


class TestPlayback:
    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_play_sets_playing_state(self, mock_device_cls: MagicMock) -> None:
        player = AudioPlayer()
        player.play(_make_wav_bytes(), AudioFormat.WAV)
        assert player.is_playing is True
        mock_device_cls.return_value.start.assert_called_once()

    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_play_when_already_playing_raises(self, mock_device_cls: MagicMock) -> None:
        player = AudioPlayer()
        player.play(_make_wav_bytes(), AudioFormat.WAV)
        with pytest.raises(AlreadyPlayingError):
            player.play(_make_wav_bytes(), AudioFormat.WAV)

    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_callback_feeds_audio_data(self, mock_device_cls: MagicMock) -> None:
        wav_data = _make_wav_bytes(n_frames=512)
        player = AudioPlayer()
        player.play(wav_data, AudioFormat.WAV)

        gen = _get_generator(mock_device_cls)
        result = gen.send(512)

        assert len(result) > 0
        assert result != b"\x00" * (512 * 2)

    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_callback_pads_silence_at_end(self, mock_device_cls: MagicMock) -> None:
        wav_data = _make_wav_bytes(n_frames=256)
        player = AudioPlayer()
        player.play(wav_data, AudioFormat.WAV)

        gen = _get_generator(mock_device_cls)
        # First send consumes all 256 frames
        result1 = gen.send(256)
        assert len(result1) == 256 * 2

        # Second send returns padded silence (end of data)
        result2 = gen.send(256)
        assert result2 == b"\x00" * (256 * 2)

        # Third send triggers _on_stream_finished and StopIteration
        with pytest.raises(StopIteration):
            gen.send(256)

        assert player.is_playing is False

    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_on_finished_called_after_natural_completion(
        self, mock_device_cls: MagicMock
    ) -> None:
        finished = []
        player = AudioPlayer()
        player.play(
            _make_wav_bytes(n_frames=256),
            AudioFormat.WAV,
            on_finished=lambda: finished.append(True),
        )

        gen = _get_generator(mock_device_cls)
        gen.send(256)
        gen.send(256)
        with pytest.raises(StopIteration):
            gen.send(256)

        assert player.is_playing is False
        assert len(finished) == 1

    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_natural_completion_detaches_stream(
        self, mock_device_cls: MagicMock
    ) -> None:
        player = AudioPlayer()
        player.play(_make_wav_bytes(n_frames=256), AudioFormat.WAV)

        gen = _get_generator(mock_device_cls)
        gen.send(256)
        gen.send(256)
        with pytest.raises(StopIteration):
            gen.send(256)

        assert player.is_playing is False
        assert player._stream is None

    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_can_play_multiple_times(self, mock_device_cls: MagicMock) -> None:
        player = AudioPlayer()

        player.play(_make_wav_bytes(n_frames=256), AudioFormat.WAV)
        gen = _get_generator(mock_device_cls)
        gen.send(256)
        gen.send(256)
        with pytest.raises(StopIteration):
            gen.send(256)
        assert player.is_playing is False

        player.play(_make_wav_bytes(n_frames=256), AudioFormat.WAV)
        assert player.is_playing is True

    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_creates_stream_with_correct_params(
        self, mock_device_cls: MagicMock
    ) -> None:
        wav_data = _make_wav_bytes(sample_rate=24_000, channels=1)
        player = AudioPlayer()
        player.play(wav_data, AudioFormat.WAV)

        call_kwargs = mock_device_cls.call_args.kwargs
        assert call_kwargs["sample_rate"] == 24_000
        assert call_kwargs["nchannels"] == 1
        assert call_kwargs["output_format"] == ma.SampleFormat.SIGNED16


class TestStop:
    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_stop_closes_stream(self, mock_device_cls: MagicMock) -> None:
        player = AudioPlayer()
        player.play(_make_wav_bytes(), AudioFormat.WAV)
        player.stop()
        mock_device_cls.return_value.close.assert_called_once()

    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_finished_callback_resets_state(self, mock_device_cls: MagicMock) -> None:
        player = AudioPlayer()
        player.play(_make_wav_bytes(n_frames=256), AudioFormat.WAV)

        gen = _get_generator(mock_device_cls)
        gen.send(256)
        gen.send(256)
        with pytest.raises(StopIteration):
            gen.send(256)

        assert player.is_playing is False

    def test_stop_when_not_playing_is_noop(self) -> None:
        player = AudioPlayer()
        player.stop()
        assert player.is_playing is False

    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_stop_triggers_on_finished_via_callback(
        self, mock_device_cls: MagicMock
    ) -> None:
        finished = []
        player = AudioPlayer()
        player.play(
            _make_wav_bytes(n_frames=256),
            AudioFormat.WAV,
            on_finished=lambda: finished.append(True),
        )

        player.stop()

        assert len(finished) == 1


class TestUnsupportedFormat:
    def test_unsupported_format_raises(self) -> None:
        player = AudioPlayer()
        with pytest.raises(UnsupportedAudioFormatError):
            player.play(b"fake data", "mp3")  # type: ignore[arg-type]


class TestGuardAudioOutput:
    def test_raises_when_no_miniaudio(self) -> None:
        with patch("vibe.cli.audio_player.audio_player.ma", None):
            player = AudioPlayer()
            with pytest.raises(AudioBackendUnavailableError):
                player.play(_make_wav_bytes(), AudioFormat.WAV)

    @patch("vibe.cli.audio_player.audio_player.ma.PlaybackDevice")
    def test_raises_when_no_output_device(self, mock_device_cls: MagicMock) -> None:
        with patch("vibe.cli.audio_player.audio_player.ma.Devices") as mock_devices_cls:
            mock_devices_cls.return_value.get_playbacks.return_value = []
            player = AudioPlayer()
            with pytest.raises(NoAudioOutputDeviceError):
                player.play(_make_wav_bytes(), AudioFormat.WAV)
            assert player.is_playing is False
            mock_device_cls.assert_not_called()
