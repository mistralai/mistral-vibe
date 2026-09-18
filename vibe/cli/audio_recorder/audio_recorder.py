from __future__ import annotations

import array
import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
import io
import struct
import threading
import time
from typing import TYPE_CHECKING
import wave

from vibe.cli.audio_recorder.audio_recorder_port import (
    AlreadyRecordingError,
    AudioBackendUnavailableError,
    AudioRecording,
    NoAudioInputDeviceError,
    RecordingMode,
)
from vibe.observability.logging import logger

# miniaudio raises OSError or ImportError on import when no audio driver is available.
_MA_IMPORT_ERROR: OSError | ImportError | None = None
try:
    import miniaudio as ma

    if TYPE_CHECKING:
        from miniaudio import CaptureDevice
except (OSError, ImportError) as e:
    logger.warning("miniaudio unavailable, voice disabled: %r", e)
    _MA_IMPORT_ERROR = e
    ma = None  # type: ignore[assignment]

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_CHANNELS = 1
DEFAULT_BUFFER_MS = 200
DEFAULT_SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
INT16_ABS_MAX = 2**15 - 1
# A denied or muted microphone returns pure-silence buffers,
# so any block peak above this floor means a real signal reached us.
SILENCE_PEAK_THRESHOLD = 0.001
DRAIN_TIMEOUT = 5.0
DEFAULT_MAX_DURATION = 300.0  # 5 min


class AudioRecorder:
    """Records audio from the default microphone using miniaudio.

    Supports both buffer mode (stop returns WAV bytes) and streaming
    mode (async generator yields chunks).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode: RecordingMode = RecordingMode.BUFFER
        self._sample_rate: int = 0
        self._channels: int = DEFAULT_CHANNELS
        self._stream: CaptureDevice | None = None
        self._frames: list[bytes] = []
        self._peak: float = 0.0
        self._signal_detected: bool = False
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

    @property
    def has_signal(self) -> bool:
        """Whether any block since ``start`` exceeded the silence floor."""
        return self._signal_detected

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

            if not ma:
                error_message = "miniaudio is not available, audio recording disabled"
                if _MA_IMPORT_ERROR:
                    error_message = f"{error_message}: {_MA_IMPORT_ERROR}"
                logger.error(error_message)
                raise AudioBackendUnavailableError(error_message) from _MA_IMPORT_ERROR

            try:
                self._guard_audio_input()
            except NoAudioInputDeviceError as exc:
                logger.error("No audio input device available, recording disabled")
                raise exc

            self._mode = mode
            self._sample_rate = sample_rate
            self._channels = channels
            self._peak = 0.0
            self._signal_detected = False
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

            gen = self._capture_generator()
            next(gen)  # prime the generator
            self._stream = ma.CaptureDevice(
                input_format=ma.SampleFormat.SIGNED16,
                nchannels=channels,
                sample_rate=sample_rate,
                buffersize_msec=DEFAULT_BUFFER_MS,
            )
            self._stream.start(gen)
            self._recording = True

            self._on_expire = on_expire
            self._start_max_duration_timer(max_duration)

    def stop(self, *, wait_for_queue_drained: bool = True) -> AudioRecording:
        with self._lock:
            if not self._recording or not self._stream:
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
        if wait_for_queue_drained and self._audio_queue_drained and not on_event_loop:
            self._audio_queue_drained.wait(timeout=DRAIN_TIMEOUT)
        return AudioRecording(data=b"", duration=duration)

    def cancel(self) -> None:
        with self._lock:
            if not self._recording or not self._stream:
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
        if not queue:
            return
        audio_queue_drained = self._audio_queue_drained

        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            if audio_queue_drained:
                audio_queue_drained.set()

    def _capture_generator(self) -> Generator[None, bytes | array.array, None]:
        """Generator that receives captured audio data from miniaudio.

        miniaudio sends ``array.array`` objects; we convert to ``bytes``
        for downstream processing.
        """
        try:
            while True:
                data = yield
                raw = bytes(data)
                self._process_audio(raw)
        except GeneratorExit:
            pass

    def _process_audio(self, raw: bytes) -> None:
        """Process a chunk of raw PCM int16 data."""
        n_samples = len(raw) // DEFAULT_SAMPLE_WIDTH
        if n_samples > 0:
            samples = struct.unpack(f"<{n_samples}h", raw)
            self._peak = min(max(abs(s) for s in samples) / INT16_ABS_MAX, 1.0)
            if self._peak > SILENCE_PEAK_THRESHOLD:
                self._signal_detected = True

        if self._mode == RecordingMode.BUFFER:
            self._frames.append(raw)

        if self._mode == RecordingMode.STREAM and self._loop and self._audio_queue:
            self._loop.call_soon_threadsafe(self._audio_queue.put_nowait, raw)

    def _stop_stream(self) -> None:
        if self._stream:
            self._stream.close()
            self._stream = None

    def _push_sentinel(self) -> None:
        """Push None to the audio queue to signal end-of-stream to the consumer."""
        if self._loop and self._audio_queue:
            self._loop.call_soon_threadsafe(self._audio_queue.put_nowait, None)
        self._audio_queue = None
        self._loop = None

    def _guard_audio_input(self) -> None:
        """Verify that at least one capture device is available."""
        if not ma:
            raise RuntimeError("miniaudio is not available")
        try:
            devices = ma.Devices()
            captures = devices.get_captures()
        except Exception as exc:
            raise NoAudioInputDeviceError("No audio input device available") from exc
        if not captures:
            raise NoAudioInputDeviceError("No audio input device available")

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
        if self._on_expire:
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
        if not self._max_duration_timer:
            return

        self._max_duration_timer.cancel()
        self._max_duration_timer = None
