from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

from vibe.core.watchdog.event_adapter import PendingWatchdogEvent

_MIN_CAPACITY = 2


@dataclass(frozen=True, slots=True)
class QueuePutResult:
    accepted: bool
    integrity_lost: bool = False


class WatchdogEventQueue:
    def __init__(self, *, capacity: int = 256, critical_reserve: int = 32) -> None:
        if (
            capacity < _MIN_CAPACITY
            or critical_reserve < 1
            or critical_reserve >= capacity
        ):
            raise ValueError("queue capacity must leave room for both event classes")
        self._capacity = capacity
        self._progress_limit = capacity - critical_reserve
        self._items: deque[PendingWatchdogEvent] = deque()
        self._available = asyncio.Event()
        self._closed = False

    def put_nowait(self, event: PendingWatchdogEvent) -> QueuePutResult:
        if self._closed:
            return QueuePutResult(accepted=False, integrity_lost=event.critical)
        if event.coalesce_key is not None and self._replace_coalesced(event):
            return QueuePutResult(accepted=True)
        if not event.critical and len(self._items) >= self._progress_limit:
            return QueuePutResult(accepted=False)
        if len(self._items) >= self._capacity:
            return QueuePutResult(accepted=False, integrity_lost=event.critical)
        self._items.append(event)
        self._available.set()
        return QueuePutResult(accepted=True)

    async def get(self) -> PendingWatchdogEvent | None:
        while not self._items:
            if self._closed:
                return None
            self._available.clear()
            await self._available.wait()
        item = self._items.popleft()
        if not self._items:
            self._available.clear()
        return item

    def close(self) -> None:
        self._closed = True
        self._available.set()

    def _replace_coalesced(self, event: PendingWatchdogEvent) -> bool:
        for index in range(len(self._items) - 1, -1, -1):
            existing = self._items[index]
            if existing.coalesce_key == event.coalesce_key:
                self._items[index] = event
                return True
        return False
