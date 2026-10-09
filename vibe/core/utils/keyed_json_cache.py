"""Per-key JSON cache files with a TTL, written atomically.

Shared by the experiment-eval cache and the model-catalog cache: one file
per feature, entries keyed by a per-user key (the hashed Mistral API key),
each entry stamped with ``stored_at_timestamp`` and a payload. A stale or
corrupt entry is never fatal — the caller gets None and falls back.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import time
from typing import Any

from vibe.observability.logging import logger


def load_keyed_entry(
    path: Path, key: str, *, ttl_seconds: int, parse: Callable[[Any], Any | None]
) -> Any | None:
    """Return the fresh entry for ``key``, parsed by ``parse``.

    ``parse`` receives the stored payload and returns the typed value or
    None when it cannot be used; a corrupt payload is the caller's to log.
    """
    entry = _read_entries(path).get(key)
    if not isinstance(entry, dict):
        return None
    stored_at = entry.get("stored_at_timestamp")
    payload = entry.get("payload")
    if not isinstance(stored_at, int):
        return None
    if stored_at <= int(time.time()) - ttl_seconds:
        return None
    return parse(payload)


def store_keyed_entry(path: Path, key: str, payload: Any) -> None:
    """Insert or overwrite the entry for ``key``; never raises."""
    entries = _read_entries(path)
    entries[key] = {"stored_at_timestamp": int(time.time()), "payload": payload}
    _write_entries(path, entries)


def _read_entries(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_entries(path: Path, entries: dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(entries, f, separators=(",", ":"))
        os.replace(tmp_path, path)
    except (OSError, TypeError):
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.debug("Failed to write keyed cache file %s", path, exc_info=True)
