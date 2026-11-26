from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import time

__all__ = ["iso_from_time_ms", "now_ms", "optional_time_ms", "time_ms"]


def now_ms() -> int:
    return int(time.time() * 1000)


def _parse_time_ms(value: str) -> int | None:
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return None


def optional_time_ms(value: str | None) -> int | None:
    if value is None:
        return None
    return _parse_time_ms(value)


def time_ms(value: str, *, fallback: Callable[[], int] = now_ms) -> int:
    timestamp = _parse_time_ms(value)
    return timestamp if timestamp is not None else fallback()


def iso_from_time_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()
