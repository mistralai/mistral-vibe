from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from vibe.core.transcribe.transcribe_client_port import TranscribeEvent


class FakeTranscribeClient:
    def __init__(
        self, *_args: Any, events: list[TranscribeEvent] | None = None, **_kwargs: Any
    ) -> None:
        self._events: list[TranscribeEvent] = events or []

    def set_events(self, events: list[TranscribeEvent]) -> None:
        self._events = events

    async def transcribe(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscribeEvent]:
        for event in self._events:
            yield event
