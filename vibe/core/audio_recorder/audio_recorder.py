from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
import io
import struct
import threading
import time
from typing import TYPE_CHECKING
import wave

from vibe.core.audio_recorder.audio_recorder_port import (
    AlreadyRecordingError,
    AudioBackendUnavailableError,
    AudioRecording,
    IncompatibleSampleRateError,
    NoAudioInputDeviceError,
    RecordingMode,
)
from vibe.core.logger import logger

# sounddevice raises OSError on import when no audio driver is available.
try:
    import sounddevice as sd

    if TYPE_CHECKING:
        from sounddevice import CallbackFlags, RawInputStream
except OSError:
    sd = None  # type: ignore[assignment]

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_CHANNELS = 1
DTYPE = "int16"
DEFAULT_BLOCKSIZE = 4096
DEFAULT_SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
INT16_ABS_MAX = 2**15 - 1
DRAIN_TIMEOUT = 5.0
DEFAULT_MAX_DURATION = 300.0  # 5 min


class AudioRecorder:
    """Records audio from the default microphone using sounddevice.

    Supports both buffer mode (stop returns WAV bytes) and streaming
    mode (async generator yields chunks).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode: RecordingMode = RecordingMode.BUFFER
        self._stream: RawInputStream | None = None
        self._frames: list[bytes] = []
        self._peak: float = 0.0
        self._recording: bool = False
        self._start_time: float = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._audio_queue: asyncio.Queue[bytes | None] | None = None
        self._audio_queue_drained: threading.Event | None = None
        self._max_duration_timer: threading.Timer | None = None
        self._on_expire: Callable[[AudioRecording], object] | None = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def mode(self) -> RecordingMode:
        return self._mode

    @property
    def peak(self) -> float:
        """Current audio peak level normalized to [0.0, 1.0], updated per audio block."""
        return self._peak

    def start(
        self,
        mode: RecordingMode,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        max_duration: float = DEFAULT_MAX_DURATION,
        on_expire: Callable[[AudioRecording], object] | None = None,
    ) -> None:
        with self._lock:
            if self._recording:
                raise AlreadyRecordingError("Already recording")

            if not sd:
                error_message = "sounddevice is not available, audio recording disabled"
                logger.error(error_message)
                raise AudioBackendUnavailableError(error_message)

            try:
                sample_rate = self._guard_audio_input(sample_rate, channels)
            except NoAudioInputDeviceError as exc:
                logger.error("No audio input device available, recording disabled")
                raise exc
            except IncompatibleSampleRateError as exc:
                logger.warning(
                    "Requested sample rate %d Hz not supported, falling back to %d Hz",
                    sample_rate,
                    exc.fallback_sample_rate,
                )
                sample_rate = exc.fallback_sample_rate

            self._mode = mode
            self._sample_rate = sample_rate
            self._channels = channels
            self._peak = 0.0
            self._start_time = time.monotonic()
            self._frames = []

            if mode == RecordingMode.BUFFER:
                self._audio_queue = None
                self._loop = None
                self._audio_queue_drained = None
            else:
                self._audio_queue_drained = threading.Event()
                try:
                    self._loop = asyncio.get_running_loop()
                    self._audio_queue = asyncio.Queue()
                except RuntimeError:
                    self._loop = None
                    self._audio_queue = None

            self._stream = sd.RawInputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype=DTYPE,
                blocksize=DEFAULT_BLOCKSIZE,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._recording = True

            self._on_expire = on_expire
            self._start_max_duration_timer(max_duration)

    def stop(self, *, wait_for_queue_drained: bool = True) -> AudioRecording:
        with self._lock:
            if not self._recording or self._stream is None:
                return AudioRecording(data=b"", duration=0.0)

            self._reset_max_duration_timer()

            self._stop_stream()
            self._recording = False
            duration = time.monotonic() - self._start_time

            if self._mode == RecordingMode.BUFFER:
                wav_data = self._encode_wav()
                self._frames = []
                return AudioRecording(data=wav_data, duration=duration)

        loop = self._loop
        self._push_sentinel()
        try:
            on_event_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_event_loop = False
        if (
            wait_for_queue_drained
            and self._audio_queue_drained is not None
            and not on_event_loop
        ):
            self._audio_queue_drained.wait(timeout=DRAIN_TIMEOUT)
        return AudioRecording(data=b"", duration=duration)

    def cancel(self) -> None:
        with self._lock:
            if not self._recording or self._stream is None:
                return

            self._reset_max_duration_timer()
            self._stop_stream()
            self._recording = False

            if self._mode == RecordingMode.BUFFER:
                self._frames = []
            else:
                self._push_sentinel()

    async def audio_stream(self) -> AsyncGenerator[bytes, None]:
        queue = self._audio_queue
        if queue is None:
            return
        audio_queue_drained = self._audio_queue_drained

        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            if audio_queue_drained is not None:
                audio_queue_drained.set()

    def _audio_callback(
        self, indata: bytes, frames: int, time_info: object, status: CallbackFlags
    ) -> None:
        if status:
            logger.warning("Audio callback status: %s", status)

        raw = bytes(indata)

        n_samples = frames * self._channels
        if n_samples > 0:
            samples = struct.unpack(f"<{n_samples}h", raw)
            self._peak = min(max(abs(s) for s in samples) / INT16_ABS_MAX, 1.0)

        if self._mode == RecordingMode.BUFFER:
            self._frames.append(raw)

        if (
            self._mode == RecordingMode.STREAM
            and self._loop is not None
            and self._audio_queue is not None
        ):
            self._loop.call_soon_threadsafe(self._audio_queue.put_nowait, raw)

    def _stop_stream(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _push_sentinel(self) -> None:
        """Push None to the audio queue to signal end-of-stream to the consumer."""
        if self._loop is not None and self._audio_queue is not None:
            self._loop.call_soon_threadsafe(self._audio_queue.put_nowait, None)
        self._audio_queue = None
        self._loop = None

    @staticmethod
    def _guard_audio_input(sample_rate: int, channels: int) -> int:
        if sd is None:
            raise RuntimeError("sounddevice is not available")
        try:
            device_info = sd.query_devices(kind="input")
        except Exception as exc:
            raise NoAudioInputDeviceError("No audio input device available") from exc

        try:
            sd.check_input_settings(
                samplerate=sample_rate, channels=channels, dtype=DTYPE
            )
        except sd.PortAudioError as exc:
            fallback = int(device_info["default_samplerate"])
            raise IncompatibleSampleRateError(
                f"Requested sample rate {sample_rate} Hz is not supported by the default "
                f"input device; device default is {fallback} Hz",
                fallback_sample_rate=fallback,
            ) from exc

        return sample_rate

    def _encode_wav(self) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(DEFAULT_SAMPLE_WIDTH)
            wf.setframerate(self._sample_rate)
            wf.writeframes(b"".join(self._frames))
        return buf.getvalue()

    def _on_max_duration_expired(self) -> None:
        result = self.stop()
        if self._on_expire is not None:
            self._on_expire(result)

    def _start_max_duration_timer(self, max_duration: float) -> None:
        if max_duration <= 0:
            return

        self._max_duration_timer = threading.Timer(
            max_duration, self._on_max_duration_expired
        )
        self._max_duration_timer.daemon = True
        self._max_duration_timer.start()

    def _reset_max_duration_timer(self) -> None:
        if self._max_duration_timer is None:
            return

        self._max_duration_timer.cancel()
        self._max_duration_timer = None
