"""Reading a cache another Vibe version may have written.

The cache file is shared by every Vibe process, and a CLI and a Desktop app
server can be different versions. A row this version cannot read (a newer
``kind``, say) is dropped on its own, and an unknown size kind reads as the
conservative one, so one new value never discards every number (ADR 0014).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from vibe.app_server.protocol import (
    StorageOtherUsage,
    StorageSessionUsage,
    StorageUsageSnapshot,
    StorageWorktreeUsage,
)

_ROWS: dict[str, type[BaseModel]] = {
    "sessions": StorageSessionUsage,
    "worktrees": StorageWorktreeUsage,
    "other": StorageOtherUsage,
}
_SIZE_KINDS = frozenset({"private", "allocated"})


def parse_snapshot(raw: object) -> StorageUsageSnapshot:
    """Raises ``ValueError`` only when the snapshot itself is unreadable."""
    if not isinstance(raw, dict):
        raise ValueError("The storage usage snapshot is not an object")
    cleaned: dict[str, Any] = dict(raw)
    for key, model in _ROWS.items():
        rows = cleaned.get(key)
        if isinstance(rows, list):
            cleaned[key] = [row for row in rows if _readable(model, row)]
    if cleaned.get("sizeKind") not in _SIZE_KINDS:
        cleaned["sizeKind"] = "allocated"
    return StorageUsageSnapshot.model_validate(cleaned, by_alias=True, extra="ignore")


def _readable(model: type[BaseModel], row: object) -> bool:
    try:
        model.model_validate(row, by_alias=True, extra="ignore")
    except ValidationError:
        return False
    return True


__all__ = ["parse_snapshot"]
