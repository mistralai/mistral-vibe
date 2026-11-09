from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum, auto
from typing import Protocol


class AudioFormat(StrEnum):
    WAV = auto()


class AlreadyPlayingError(Exception):
    pass


class AudioBackendUnavailableError(Exception):
    pass


class NoAudioOutputDeviceError(Exception):
    pass


class UnsupportedAudioFormatError(Exception):
    pass


class AudioPlayerPort(Protocol):
    @property
    def is_playing(self) -> bool: ...

    def play(
        self,
        audio_data: bytes,
        audio_format: AudioFormat,
        *,
        on_finished: Callable[[], object] | None = ...,
    ) -> None: ...

    def stop(self) -> None: ...
