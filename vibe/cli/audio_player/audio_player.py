from __future__ import annotations

from collections.abc import Callable, Generator
import threading
from typing import TYPE_CHECKING

from vibe.cli.audio_player.audio_player_port import (
    AlreadyPlayingError,
    AudioBackendUnavailableError,
    AudioFormat,
    NoAudioOutputDeviceError,
    UnsupportedAudioFormatError,
)
from vibe.cli.audio_player.utils import decode_wav
from vibe.observability.logging import logger

# miniaudio raises OSError or ImportError on import when no audio driver is available.
try:
    import miniaudio as ma

    if TYPE_CHECKING:
        from miniaudio import PlaybackDevice
except (OSError, ImportError) as e:
    logger.warning("miniaudio unavailable, voice disabled: %r", e)
    ma = None  # type: ignore[assignment]

DEFAULT_BUFFER_MS = 200
DEFAULT_SAMPLE_WIDTH = 2  # 16-bit = 2 bytes


def check_audio_available() -> str | None:
    """Return an error string if miniaudio is unavailable, else None."""
    if not ma:
        return (
            "miniaudio is not installed or no audio backend is available "
            "(install the 'miniaudio' package)."
        )
    try:
        devices = ma.Devices()
        playbacks = devices.get_playbacks()
        if not playbacks:
            return "No audio output device available."
    except Exception as exc:
        return f"No audio output device available: {exc}"
    return None


class AudioPlayer:
    """Plays audio through the default output device using miniaudio."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: PlaybackDevice | None = None
        self._playing: bool = False
        self._audio_data: bytes = b""
        self._position: int = 0
        self._frame_size: int = 0
        self._on_finished: Callable[[], object] | None = None

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(
        self,
        audio_data: bytes,
        audio_format: AudioFormat,
        *,
        on_finished: Callable[[], object] | None = None,
    ) -> None:
        with self._lock:
            if self._playing:
                raise AlreadyPlayingError("Already playing")

            # Close any device left from a previous natural completion
            if self._stream:
                self._stream.close()
                self._stream = None

            if not ma:
                error_message = "miniaudio is not available, audio playback disabled"
                logger.error(error_message)
                raise AudioBackendUnavailableError(error_message)

            self._guard_audio_output()

            match audio_format:
                case AudioFormat.WAV:
                    sample_rate, channels, pcm_data = decode_wav(audio_data)
                case _:
                    raise UnsupportedAudioFormatError(
                        f"Unsupported audio format: {audio_format}"
                    )
            self._audio_data = pcm_data
            self._position = 0
            self._frame_size = channels * DEFAULT_SAMPLE_WIDTH
            self._on_finished = on_finished

            gen = self._playback_generator()
            next(gen)  # prime the generator
            self._stream = ma.PlaybackDevice(
                output_format=ma.SampleFormat.SIGNED16,
                nchannels=channels,
                sample_rate=sample_rate,
                buffersize_msec=DEFAULT_BUFFER_MS,
            )
            self._stream.start(gen)
            self._playing = True

    def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream:
            stream.close()
        self._on_stream_finished()

    def _playback_generator(self) -> Generator[bytes, int, None]:
        """Generator that provides audio data to miniaudio for playback.

        miniaudio sends the requested number of frames; we yield the
        corresponding PCM bytes. When data is exhausted, we signal
        completion via ``_on_stream_finished`` and stop the generator.
        """
        try:
            num_frames = yield b""  # priming (receives first frame count request)
            while True:
                bytes_needed = num_frames * self._frame_size
                chunk = self._audio_data[self._position : self._position + bytes_needed]
                self._position += len(chunk)
                if len(chunk) < bytes_needed:
                    # Last chunk - pad remaining with silence
                    result = chunk + b"\x00" * (bytes_needed - len(chunk))
                    yield result
                    self._on_stream_finished()
                    return
                num_frames = yield chunk
        except GeneratorExit:
            pass

    def _on_stream_finished(self) -> None:
        """Idempotent cleanup: clears playing state and invokes the finished callback.

        Called from the miniaudio data-callback thread on natural completion, so
        we detach the device but cannot call ``close()`` here (it would deadlock).
        The next ``play()`` or ``stop()`` closes any leftover device.
        """
        on_finished = None
        with self._lock:
            if not self._playing:
                return
            self._playing = False
            self._stream = None
            on_finished = self._on_finished

        if on_finished:
            on_finished()

    @staticmethod
    def _guard_audio_output() -> None:
        if not ma:
            raise RuntimeError("miniaudio is not available")
        try:
            devices = ma.Devices()
            playbacks = devices.get_playbacks()
        except Exception as exc:
            raise NoAudioOutputDeviceError("No audio output device available") from exc
        if not playbacks:
            raise NoAudioOutputDeviceError("No audio output device available")
