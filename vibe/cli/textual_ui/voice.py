from __future__ import annotations

import io
import struct
import threading
from typing import TYPE_CHECKING, Any
import wave

from vibe.core.utils import logger

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM
_INT16_MAX = 32_767

if TYPE_CHECKING:
    import sounddevice as sd

try:
    import sounddevice as _sd

    SOUNDDEVICE_AVAILABLE = True
except (ImportError, OSError):
    _sd = None
    SOUNDDEVICE_AVAILABLE = False


def _create_raw_input_stream(
    sample_rate: int, channels: int, callback: Any
) -> sd.RawInputStream:
    """Create a sounddevice RawInputStream.

    Separated into a helper so the module-level ``_sd`` reference is only
    accessed after an availability check, keeping pyright happy.
    """
    if _sd is None:
        raise RuntimeError(
            "sounddevice is not installed. Install it with: uv add sounddevice"
        )
    stream: sd.RawInputStream = _sd.RawInputStream(
        samplerate=sample_rate, channels=channels, dtype="int16", callback=callback
    )
    return stream


class AudioRecorder:
    """Records audio from the default microphone into a WAV buffer.

    Uses sounddevice in callback mode so the recording runs on a
    PortAudio background thread -- no numpy required at the call-site.
    """

    def __init__(
        self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunks: list[bytes] = []
        self._stream: sd.RawInputStream | None = None
        self._lock = threading.Lock()
        self._recording = False
        self._peak: float = 0.0  # 0.0 – 1.0, updated from audio thread

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def peak(self) -> float:
        """Most recent peak amplitude normalised to 0.0 – 1.0."""
        return self._peak

    def start(self) -> None:
        """Begin capturing audio from the default input device."""
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError(
                "sounddevice is not installed. Install it with: uv add sounddevice"
            )

        with self._lock:
            if self._recording:
                return
            self._chunks.clear()
            self._peak = 0.0
            self._stream = _create_raw_input_stream(
                self._sample_rate, self._channels, self._audio_callback
            )
            self._stream.start()
            self._recording = True
            logger.debug("Voice recording started")

    def stop(self) -> bytes:
        """Stop recording and return WAV-encoded bytes."""
        with self._lock:
            if not self._recording or self._stream is None:
                return b""
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self._recording = False
            raw_audio = b"".join(self._chunks)
            self._chunks.clear()

        logger.debug("Voice recording stopped, %d bytes of raw audio", len(raw_audio))
        return _encode_wav(raw_audio, self._sample_rate, self._channels, SAMPLE_WIDTH)

    def cancel(self) -> None:
        """Stop recording and discard audio data."""
        with self._lock:
            if not self._recording or self._stream is None:
                return
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self._recording = False
            self._chunks.clear()
            logger.debug("Voice recording cancelled")

    def _audio_callback(
        self, indata: bytes, frames: int, time_info: object, status: object
    ) -> None:
        raw = bytes(indata)
        self._chunks.append(raw)
        # Compute peak from int16 samples – fast enough for the audio thread.
        if raw:
            n_samples = len(raw) // 2
            samples = struct.unpack(f"<{n_samples}h", raw)
            self._peak = min(max(abs(s) for s in samples) / _INT16_MAX, 1.0)


def _encode_wav(
    raw_audio: bytes, sample_rate: int, channels: int, sample_width: int
) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(raw_audio)
    return buf.getvalue()
