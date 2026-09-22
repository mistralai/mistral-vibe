"""Live event delivery for one loaded Harness Session."""

import asyncio
from collections.abc import AsyncIterator

from mistralai_vibe_local_harness.session_protocol import JsonObject


class SessionEventSubscriptions:
    """Fan out future Session events to each subscription independently."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[JsonObject | None]] = set()
        self._closed = False

    def subscribe(self) -> AsyncIterator[JsonObject]:
        queue: asyncio.Queue[JsonObject | None] = asyncio.Queue()
        if self._closed:
            queue.put_nowait(None)
        else:
            self._subscribers.add(queue)
        return self._event_stream(queue)

    def publish(self, event: JsonObject) -> None:
        if self._closed:
            return
        for queue in tuple(self._subscribers):
            queue.put_nowait(event)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        subscribers = tuple(self._subscribers)
        self._subscribers.clear()
        for queue in subscribers:
            queue.put_nowait(None)

    async def _event_stream(
        self, queue: asyncio.Queue[JsonObject | None]
    ) -> AsyncIterator[JsonObject]:
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            self._subscribers.discard(queue)


__all__ = ["SessionEventSubscriptions"]
