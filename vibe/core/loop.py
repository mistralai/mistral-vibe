from __future__ import annotations

from enum import StrEnum, auto
import math
import re
import secrets
import time
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from vibe.core.logger import logger
from vibe.core.types import ScheduledLoop

if TYPE_CHECKING:
    from vibe.core.session.session_logger import SessionLogger

__all__ = [
    "MAX_LOOPS_PER_SESSION",
    "MIN_INTERVAL_SECONDS",
    "USAGE_HINT",
    "IntervalUnit",
    "LoopCommandResult",
    "LoopError",
    "LoopErrorResult",
    "LoopListResult",
    "LoopManager",
    "LoopOkResult",
    "format_duration",
    "parse_interval",
]


MIN_INTERVAL_SECONDS = 30
MAX_LOOPS_PER_SESSION = 50
USAGE_HINT = """\
Usage:
  /loop <interval> <prompt>
  /loop list
  /loop cancel <id|all>
"""

_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")
_CANCEL_VERBS = frozenset({"cancel", "rm", "stop", "delete"})
_LIST_VERBS = frozenset({"list", "ls"})


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

    @property
    def suffix(self) -> str:
        match self:
            case IntervalUnit.SECOND:
                return "s"
            case IntervalUnit.MINUTE:
                return "m"
            case IntervalUnit.HOUR:
                return "h"
            case IntervalUnit.DAY:
                return "d"

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


def format_duration(seconds: int, short: bool = False) -> str:
    parts = []
    for unit in (
        IntervalUnit.DAY,
        IntervalUnit.HOUR,
        IntervalUnit.MINUTE,
        IntervalUnit.SECOND,
    ):
        value = seconds // unit.seconds
        if value > 0:
            parts.append(f"{value}{unit.suffix}")
            seconds %= unit.seconds
    if not parts:
        parts = ["0s"]
    if short:
        return parts[0]
    return "".join(parts)


class _LoopResultBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LoopListResult(_LoopResultBase):
    loops: list[ScheduledLoop]


class LoopOkResult(_LoopResultBase):
    message: str


class LoopErrorResult(_LoopResultBase):
    message: str


LoopCommandResult = LoopListResult | LoopOkResult | LoopErrorResult


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

    async def pop_due(self, now: float | None = None) -> ScheduledLoop | None:
        if not self._loops:
            return None
        ts = now if now is not None else time.time()
        due_loops = [loop for loop in self._loops if loop.next_fire_at <= ts]
        if not due_loops:
            return None
        due = min(due_loops, key=lambda loop: loop.next_fire_at)
        due.next_fire_at = ts + due.interval_seconds
        await self._persist()
        return due

    async def handle_command(self, args: str) -> LoopCommandResult:
        text = args.strip()
        if not text:
            return self._list_result()
        verb, _, rest = text.partition(" ")
        verb_lower = verb.lower()
        if verb_lower in _LIST_VERBS:
            return self._list_result()
        if verb_lower in _CANCEL_VERBS:
            return await self._cancel(rest.strip())
        return await self._add(verb, rest)

    def _list_result(self) -> LoopListResult:
        return LoopListResult(loops=list(self._loops))

    async def _add(
        self, interval_text: str, prompt: str
    ) -> LoopOkResult | LoopErrorResult:
        try:
            seconds = parse_interval(interval_text)
        except LoopError as e:
            return LoopErrorResult(message=str(e))
        prompt = prompt.strip()
        if not prompt:
            return LoopErrorResult(message="Missing prompt.")
        if prompt.startswith("/"):
            return LoopErrorResult(message="Prompt cannot start with '/'.")
        if len(self._loops) >= MAX_LOOPS_PER_SESSION:
            return LoopErrorResult(
                message=f"Loop limit reached ({MAX_LOOPS_PER_SESSION} per session)."
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
        return LoopOkResult(
            message=(
                f"Scheduled loop `{loop.id}` every {format_duration(seconds)}: {prompt}"
            )
        )

    async def _cancel(self, target: str) -> LoopOkResult | LoopErrorResult:
        if not target:
            return LoopErrorResult(message="Missing loop id.")
        if target.lower() == "all":
            count = len(self._loops)
            self._loops.clear()
            await self._persist()
            return LoopOkResult(message=f"Cancelled {count} scheduled loop(s).")
        match = next((loop for loop in self._loops if loop.id == target), None)
        if match is None:
            return LoopErrorResult(message=f"No scheduled loop with id `{target}`.")
        self._loops.remove(match)
        await self._persist()
        return LoopOkResult(message=f"Cancelled loop `{match.id}`: {match.prompt}")

    async def _persist(self) -> None:
        metadata = self._session_logger.session_metadata
        if metadata is not None:
            metadata.loops = [*self._loops]
        try:
            await self._session_logger.persist_loops()
        except Exception as e:
            logger.error("Failed to persist scheduled loops", exc_info=e)
