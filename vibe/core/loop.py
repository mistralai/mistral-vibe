from __future__ import annotations

from enum import StrEnum, auto
import math
import re
import secrets
import time
from typing import TYPE_CHECKING

from vibe.core.types import ScheduledLoop
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.session.session_logger import SessionLogger

__all__ = [
    "MAX_LOOPS_PER_SESSION",
    "MIN_INTERVAL_SECONDS",
    "LoopError",
    "LoopManager",
    "parse_interval",
]


MIN_INTERVAL_SECONDS = 30
MAX_LOOPS_PER_SESSION = 50
_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")


class LoopError(Exception):
    pass


class IntervalUnit(StrEnum):
    SECOND = auto()
    MINUTE = auto()
    HOUR = auto()
    DAY = auto()

    @property
    def seconds(self) -> int:
        match self:
            case IntervalUnit.SECOND:
                return 1
            case IntervalUnit.MINUTE:
                return 60
            case IntervalUnit.HOUR:
                return 3600
            case IntervalUnit.DAY:
                return 86400

    @classmethod
    def from_suffix(cls, ch: str) -> IntervalUnit:
        match ch:
            case "s":
                return cls.SECOND
            case "m":
                return cls.MINUTE
            case "h":
                return cls.HOUR
            case "d":
                return cls.DAY
            case _:
                raise LoopError(f"Unknown interval unit `{ch}`.")


def parse_interval(text: str) -> int:
    if not text:
        raise LoopError("Missing interval.")
    match = _INTERVAL_RE.match(text.strip().lower())
    if match is None:
        raise LoopError(
            f"Invalid interval `{text}`. "
            "Expected: <number><unit> (e.g., 30s, 5m, 2h, 1d)."
        )
    value = int(match.group(1))
    seconds = value * IntervalUnit.from_suffix(match.group(2)).seconds
    if seconds < MIN_INTERVAL_SECONDS:
        raise LoopError(f"Interval must be at least {MIN_INTERVAL_SECONDS}s.")
    return seconds


class LoopManager:
    def __init__(self, session_logger: SessionLogger) -> None:
        self._session_logger = session_logger
        self._loops: list[ScheduledLoop] = []

    @property
    def loops(self) -> list[ScheduledLoop]:
        return list(self._loops)

    def restore(self, loops: list[ScheduledLoop]) -> None:
        self._loops = list(loops)

    def next_due_in(self, now: float | None = None) -> float:
        if not self._loops:
            return math.inf
        ts = now if now is not None else time.time()
        return max(0.0, min(loop.next_fire_at for loop in self._loops) - ts)

    def due(self, now: float | None = None) -> ScheduledLoop | None:
        ts = now if now is not None else time.time()
        return min(
            (loop for loop in self._loops if loop.next_fire_at <= ts),
            key=lambda loop: loop.next_fire_at,
            default=None,
        )

    async def mark_fired(
        self, loop_id: str, now: float | None = None
    ) -> ScheduledLoop | None:
        loop = next((item for item in self._loops if item.id == loop_id), None)
        if loop is None:
            return None
        ts = now if now is not None else time.time()
        loop.next_fire_at = ts + loop.interval_seconds
        await self._persist()
        return loop

    async def pop_due(self, now: float | None = None) -> ScheduledLoop | None:
        due = self.due(now)
        if due is None:
            return None
        return await self.mark_fired(due.id, now)

    async def create(self, interval: str, prompt: str) -> ScheduledLoop:
        seconds = parse_interval(interval)
        prompt = prompt.strip()
        if not prompt:
            raise LoopError("Missing prompt.")
        if prompt.startswith("/"):
            raise LoopError("Prompt cannot start with '/'.")
        if len(self._loops) >= MAX_LOOPS_PER_SESSION:
            raise LoopError(
                f"Loop limit reached ({MAX_LOOPS_PER_SESSION} per session)."
            )
        now = time.time()
        loop = ScheduledLoop(
            id=secrets.token_hex(4),
            interval_seconds=seconds,
            prompt=prompt,
            next_fire_at=now + seconds,
            created_at=now,
        )
        self._loops.append(loop)
        await self._persist()
        return loop

    async def delete(self, loop_id: str) -> ScheduledLoop:
        match = next((loop for loop in self._loops if loop.id == loop_id), None)
        if match is None:
            raise LoopError(f"No scheduled loop with id `{loop_id}`.")
        self._loops.remove(match)
        await self._persist()
        return match

    async def clear(self) -> int:
        count = len(self._loops)
        self._loops.clear()
        await self._persist()
        return count

    async def _persist(self) -> None:
        metadata = self._session_logger.session_metadata
        if metadata is not None:
            metadata.loops = [*self._loops]
        try:
            await self._session_logger.persist_loops()
        except Exception as e:
            logger.error("Failed to persist scheduled loops", exc_info=e)
