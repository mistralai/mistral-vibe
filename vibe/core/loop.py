from __future__ import annotations

from enum import StrEnum, auto
import re

__all__ = [
    "MAX_LOOPS_PER_SESSION",
    "MIN_INTERVAL_SECONDS",
    "LoopError",
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
