from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Protocol

from vibe.core.audio_recorder.audio_recorder_port import RecordingMode


class TranscribeState(StrEnum):
    IDLE = auto()
    RECORDING = auto()
    FLUSHING = auto()


@dataclass(frozen=True, slots=True)
class VoiceToggleResult:
    enabled: bool


class RecordingStartError(Exception):
    pass


class VoiceManagerListener:
    def on_transcribe_state_change(self, state: TranscribeState) -> None:
        pass

    def on_voice_mode_change(self, enabled: bool) -> None:
        pass

    def on_transcribe_text(self, text: str) -> None:
        pass


class VoiceManagerPort(Protocol):
    @property
    def is_enabled(self) -> bool: ...

    @property
    def transcribe_state(self) -> TranscribeState: ...

    @property
    def peak(self) -> float: ...

    def toggle_voice_mode(self) -> VoiceToggleResult: ...

    def start_recording(self, mode: RecordingMode = RecordingMode.STREAM) -> None: ...

    async def stop_recording(self) -> None: ...

    def cancel_recording(self) -> None: ...

    def add_listener(self, listener: VoiceManagerListener) -> None: ...

    def remove_listener(self, listener: VoiceManagerListener) -> None: ...
