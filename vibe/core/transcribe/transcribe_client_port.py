from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from vibe.core.config import TranscribeModelConfig, TranscribeProviderConfig


@dataclass(frozen=True, slots=True)
class TranscribeSessionCreated:
    request_id: str


@dataclass(frozen=True, slots=True)
class TranscribeTextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class TranscribeDone:
    pass


@dataclass(frozen=True, slots=True)
class TranscribeError:
    message: str


TranscribeEvent = (
    TranscribeSessionCreated | TranscribeTextDelta | TranscribeDone | TranscribeError
)


class TranscribeClientPort(Protocol):
    def __init__(
        self, provider: TranscribeProviderConfig, model: TranscribeModelConfig
    ) -> None: ...

    def transcribe(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscribeEvent]: ...
