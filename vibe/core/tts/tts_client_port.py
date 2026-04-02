from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from vibe.core.config import TTSModelConfig, TTSProviderConfig


@dataclass(frozen=True, slots=True)
class TTSResult:
    audio_data: bytes


class TTSClientPort(Protocol):
    def __init__(self, provider: TTSProviderConfig, model: TTSModelConfig) -> None: ...

    async def speak(self, text: str) -> TTSResult: ...

    async def close(self) -> None: ...
