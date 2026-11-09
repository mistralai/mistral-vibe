from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TTSResult:
    audio_data: bytes


class TTSClientPort(Protocol):
    async def speak(self, text: str) -> TTSResult: ...

    async def close(self) -> None: ...
