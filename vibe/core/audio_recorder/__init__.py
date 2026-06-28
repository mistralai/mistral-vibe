from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.audio_recorder.audio_recorder_port import (
    AlreadyRecordingError,
    AudioBackendUnavailableError,
    AudioRecorderPort,
    AudioRecording,
    IncompatibleSampleRateError,
    NoAudioInputDeviceError,
    RecordingMode,
)

if TYPE_CHECKING:
    from vibe.core.audio_recorder.audio_recorder import AudioRecorder

__all__ = [
    "AlreadyRecordingError",
    "AudioBackendUnavailableError",
    "AudioRecorder",
    "AudioRecorderPort",
    "AudioRecording",
    "IncompatibleSampleRateError",
    "NoAudioInputDeviceError",
    "RecordingMode",
]


def __getattr__(name: str) -> object:
    if name == "AudioRecorder":
        from vibe.core.audio_recorder.audio_recorder import AudioRecorder

        return AudioRecorder
    raise AttributeError(name)
