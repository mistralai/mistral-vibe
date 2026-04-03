from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Protocol


class RecordingMode(StrEnum):
    BUFFER = auto()
    STREAM = auto()


@dataclass(frozen=True, slots=True)
class AudioRecording:
    """Result of a completed recording."""

    data: bytes
    duration: float


class AlreadyRecordingError(Exception):
    pass


class AudioBackendUnavailableError(Exception):
    pass


class NoAudioInputDeviceError(Exception):
    pass


class IncompatibleSampleRateError(Exception):
    def __init__(self, message: str, fallback_sample_rate: int) -> None:
        super().__init__(message)
        self.fallback_sample_rate = fallback_sample_rate


class AudioRecorderPort(Protocol):
    @property
    def is_recording(self) -> bool: ...

    @property
    def peak(self) -> float: ...

    @property
    def mode(self) -> RecordingMode: ...

    def start(
        self,
        mode: RecordingMode,
        *,
        sample_rate: int = ...,
        channels: int = ...,
        max_duration: float = ...,
        on_expire: Callable[[AudioRecording], object] | None = ...,
    ) -> None: ...

    def stop(self, *, wait_for_queue_drained: bool = ...) -> AudioRecording: ...

    def cancel(self) -> None: ...

    def audio_stream(self) -> AsyncGenerator[bytes, None]: ...
