from __future__ import annotations

from collections.abc import Callable
import threading
from typing import TYPE_CHECKING

from vibe.core.audio_player.audio_player_port import (
    AlreadyPlayingError,
    AudioBackendUnavailableError,
    AudioFormat,
    NoAudioOutputDeviceError,
    UnsupportedAudioFormatError,
)
from vibe.core.audio_player.utils import decode_wav
from vibe.core.logger import logger

# sounddevice raises OSError on import when no audio driver is available.
try:
    import sounddevice as sd

    if TYPE_CHECKING:
        from sounddevice import CallbackFlags, RawOutputStream
except OSError:
    sd = None  # type: ignore[assignment]

DEFAULT_BLOCKSIZE = 4096
DTYPE = "int16"
DEFAULT_SAMPLE_WIDTH = 2  # 16-bit = 2 bytes


class AudioPlayer:
    """Plays audio through the default output device using sounddevice."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: RawOutputStream | None = None
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

            if not sd:
                error_message = "sounddevice is not available, audio playback disabled"
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

            self._stream = sd.RawOutputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype=DTYPE,
                blocksize=DEFAULT_BLOCKSIZE,
                callback=self._audio_callback,
                finished_callback=self._on_stream_finished,
            )
            self._stream.start()
            self._playing = True

    def stop(self) -> None:
        stream = self._stream
        if not self._playing or stream is None:
            return
        stream.close(ignore_errors=True)

    def _audio_callback(
        self, outdata: memoryview, frames: int, time_info: object, status: CallbackFlags
    ) -> None:
        if not sd:
            raise RuntimeError("sounddevice is not available")
        if status:
            logger.warning(f"Audio playback callback status: {status}")

        bytes_needed = frames * self._frame_size
        chunk = self._audio_data[self._position : self._position + bytes_needed]
        self._position += len(chunk)

        if len(chunk) < bytes_needed:
            outdata[: len(chunk)] = chunk
            outdata[len(chunk) :] = b"\x00" * (bytes_needed - len(chunk))
            raise sd.CallbackStop()
        else:
            outdata[:] = chunk

    def _on_stream_finished(self) -> None:
        on_finished = None
        with self._lock:
            self._stream = None
            self._playing = False
            on_finished = self._on_finished

        if on_finished is not None:
            on_finished()

    @staticmethod
    def _guard_audio_output() -> None:
        if sd is None:
            raise RuntimeError("sounddevice is not available")
        try:
            sd.query_devices(kind="output")
        except Exception as exc:
            raise NoAudioOutputDeviceError("No audio output device available") from exc
