from __future__ import annotations

import io
import struct
from unittest.mock import MagicMock, patch
import wave

import pytest

try:
    import sounddevice as sd
except OSError:
    pytest.skip("PortAudio library not available", allow_module_level=True)

from vibe.core.audio_player.audio_player import AudioPlayer
from vibe.core.audio_player.audio_player_port import (
    AlreadyPlayingError,
    AudioBackendUnavailableError,
    AudioFormat,
    NoAudioOutputDeviceError,
    UnsupportedAudioFormatError,
)


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


def _get_callback(mock_stream_cls: MagicMock):
    return mock_stream_cls.call_args.kwargs["callback"]


def _get_finished_callback(mock_stream_cls: MagicMock):
    return mock_stream_cls.call_args.kwargs["finished_callback"]


class TestAudioPlayerInitialState:
    def test_not_playing(self) -> None:
        player = AudioPlayer()
        assert player.is_playing is False


class TestPlayback:
    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_play_sets_playing_state(self, mock_stream_cls: MagicMock) -> None:
        player = AudioPlayer()
        player.play(_make_wav_bytes(), AudioFormat.WAV)
        assert player.is_playing is True
        mock_stream_cls.return_value.start.assert_called_once()

    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_play_when_already_playing_raises(self, mock_stream_cls: MagicMock) -> None:
        player = AudioPlayer()
        player.play(_make_wav_bytes(), AudioFormat.WAV)
        with pytest.raises(AlreadyPlayingError):
            player.play(_make_wav_bytes(), AudioFormat.WAV)

    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_callback_feeds_audio_data(self, mock_stream_cls: MagicMock) -> None:
        wav_data = _make_wav_bytes(n_frames=512)
        player = AudioPlayer()
        player.play(wav_data, AudioFormat.WAV)

        callback = _get_callback(mock_stream_cls)
        outdata = bytearray(512 * 2)  # 512 frames * 2 bytes per sample
        callback(outdata, 512, {}, sd.CallbackFlags())

        assert outdata != bytearray(512 * 2)

    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_callback_pads_silence_at_end(self, mock_stream_cls: MagicMock) -> None:
        wav_data = _make_wav_bytes(n_frames=256)
        player = AudioPlayer()
        player.play(wav_data, AudioFormat.WAV)

        callback = _get_callback(mock_stream_cls)
        # First callback consumes all 256 frames
        outdata1 = bytearray(256 * 2)
        callback(outdata1, 256, {}, sd.CallbackFlags())

        # Second callback should raise CallbackStop (no data left)
        outdata2 = bytearray(256 * 2)
        with pytest.raises(sd.CallbackStop):
            callback(outdata2, 256, {}, sd.CallbackFlags())

    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_on_finished_called_after_natural_completion(
        self, mock_stream_cls: MagicMock
    ) -> None:
        finished = []
        player = AudioPlayer()
        player.play(
            _make_wav_bytes(),
            AudioFormat.WAV,
            on_finished=lambda: finished.append(True),
        )

        finished_callback = _get_finished_callback(mock_stream_cls)
        finished_callback()

        assert player.is_playing is False
        assert len(finished) == 1

    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_can_play_multiple_times(self, mock_stream_cls: MagicMock) -> None:
        player = AudioPlayer()

        player.play(_make_wav_bytes(), AudioFormat.WAV)
        finished_callback = _get_finished_callback(mock_stream_cls)
        finished_callback()
        assert player.is_playing is False

        player.play(_make_wav_bytes(), AudioFormat.WAV)
        assert player.is_playing is True

    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_creates_stream_with_correct_params(
        self, mock_stream_cls: MagicMock
    ) -> None:
        wav_data = _make_wav_bytes(sample_rate=24_000, channels=1)
        player = AudioPlayer()
        player.play(wav_data, AudioFormat.WAV)

        call_kwargs = mock_stream_cls.call_args.kwargs
        assert call_kwargs["samplerate"] == 24_000
        assert call_kwargs["channels"] == 1
        assert call_kwargs["dtype"] == "int16"


class TestStop:
    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_stop_closes_stream(self, mock_stream_cls: MagicMock) -> None:
        player = AudioPlayer()
        player.play(_make_wav_bytes(), AudioFormat.WAV)
        player.stop()
        mock_stream_cls.return_value.close.assert_called_once()

    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_finished_callback_resets_state(self, mock_stream_cls: MagicMock) -> None:
        player = AudioPlayer()
        player.play(_make_wav_bytes(), AudioFormat.WAV)

        finished_callback = _get_finished_callback(mock_stream_cls)
        finished_callback()

        assert player.is_playing is False

    def test_stop_when_not_playing_is_noop(self) -> None:
        player = AudioPlayer()
        player.stop()
        assert player.is_playing is False

    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_stop_triggers_on_finished_via_callback(
        self, mock_stream_cls: MagicMock
    ) -> None:
        finished = []
        player = AudioPlayer()
        player.play(
            _make_wav_bytes(),
            AudioFormat.WAV,
            on_finished=lambda: finished.append(True),
        )

        # Simulate sounddevice calling finished_callback after stop
        finished_callback = _get_finished_callback(mock_stream_cls)
        finished_callback()

        assert len(finished) == 1


class TestUnsupportedFormat:
    def test_unsupported_format_raises(self) -> None:
        player = AudioPlayer()
        with pytest.raises(UnsupportedAudioFormatError):
            player.play(b"fake data", "mp3")  # type: ignore[arg-type]


class TestGuardAudioOutput:
    def test_raises_when_no_sounddevice(self) -> None:
        with patch("vibe.core.audio_player.audio_player.sd", None):
            player = AudioPlayer()
            with pytest.raises(AudioBackendUnavailableError):
                player.play(_make_wav_bytes(), AudioFormat.WAV)

    @patch("vibe.core.audio_player.audio_player.sd.RawOutputStream")
    def test_raises_when_no_output_device(self, mock_stream_cls: MagicMock) -> None:
        with patch(
            "vibe.core.audio_player.audio_player.sd.query_devices",
            side_effect=sd.PortAudioError(-1),
        ):
            player = AudioPlayer()
            with pytest.raises(NoAudioOutputDeviceError):
                player.play(_make_wav_bytes(), AudioFormat.WAV)
            assert player.is_playing is False
            mock_stream_cls.assert_not_called()
