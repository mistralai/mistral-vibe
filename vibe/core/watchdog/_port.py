from __future__ import annotations

from typing import Protocol

from vibe.core.types import BaseEvent
from vibe.core.watchdog.events import EventKind


class WatchdogObserverPort(Protocol):
    async def start(self) -> None: ...

    def observe(self, event: BaseEvent) -> None: ...

    async def finish(self, outcome: EventKind = EventKind.RUN_FINISHED) -> None: ...
