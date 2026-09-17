"""`/stress` repeats the exact history entries shown by `/demo`."""

from __future__ import annotations

_DEFAULT_COUNT = 100
_MAX_COUNT = 100_000
RATE = 10_000.0


def count(cmd_args: str) -> int:
    try:
        value = int(cmd_args.strip()) if cmd_args.strip() else _DEFAULT_COUNT
    except ValueError:
        value = _DEFAULT_COUNT
    return max(1, min(value, _MAX_COUNT))
