"""Shared history entries used by the `/demo` and `/stress` commands."""

from __future__ import annotations

from collections.abc import Iterator
from functools import cache
import json
from pathlib import Path

from vibe.app_server.models import PublicHistoryEntry, validate_history_entry
from vibe.utils.io import read_safe

_HISTORY_PATH = Path(__file__).with_name("demo_history.data")


@cache
def entries() -> tuple[PublicHistoryEntry, ...]:
    value = json.loads(read_safe(_HISTORY_PATH, raise_on_error=True).text)
    if not isinstance(value, list):
        raise ValueError("Demo history must be a JSON array")
    return tuple(validate_history_entry(entry) for entry in value)


def stress_entries(count: int) -> Iterator[PublicHistoryEntry]:
    history = entries()
    for index in range(count):
        entry = history[index % len(history)]
        yield entry.model_copy(update={"id": f"stress-{index:06}-{entry.id}"})
