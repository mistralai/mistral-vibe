from __future__ import annotations

from collections.abc import AsyncGenerator, Callable

from vibe.core.audio_recorder import (
    AlreadyRecordingError,
    AudioRecording,
    RecordingMode,
)

FAKE_WAV_DATA = b"RIFF\x00\x00\x00\x00WAVEfmt "


class FakeAudioRecorder:
    def __init__(self, *, fake_wav_data: bytes = FAKE_WAV_DATA) -> None:
        self._recording = False
        self._peak = 0.0
        self._mode = RecordingMode.BUFFER
        self._fake_wav_data = fake_wav_data
        self._stream_chunks: list[bytes] = []

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def mode(self) -> RecordingMode:
        return self._mode

    def start(
        self,
        mode: RecordingMode,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        max_duration: float = 300.0,
        on_expire: Callable[[AudioRecording], object] | None = None,
    ) -> None:
        if self._recording:
            raise AlreadyRecordingError("Already recording")
        self._recording = True
        self._mode = mode

    def stop(self, *, wait_for_queue_drained: bool = True) -> AudioRecording:
        if not self._recording:
            return AudioRecording(data=b"", duration=0.0)
        self._recording = False
        return AudioRecording(data=self._fake_wav_data, duration=1.0)

    def cancel(self) -> None:
        self._recording = False

    async def audio_stream(self) -> AsyncGenerator[bytes, None]:
        for chunk in self._stream_chunks:
            yield chunk

    def set_peak(self, value: float) -> None:
        self._peak = value

    def set_stream_chunks(self, chunks: list[bytes]) -> None:
        self._stream_chunks = chunks
