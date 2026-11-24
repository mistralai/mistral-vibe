"""Shared session rows for the Sessions page scenarios."""

from __future__ import annotations

_BASE = 1_787_821_245_000


def session(
    index: int, title: str, cwd: str = "/workspace", **extra: object
) -> dict[str, object]:
    """A listed session `…000{index}`, newer for a smaller index."""
    session_id = f"00000000-0000-4000-8000-{index:012d}"
    row: dict[str, object] = {
        "id": session_id,
        "rootSessionId": session_id,
        "parentSessionId": None,
        "title": title,
        "preview": f"Continue {title.lower()}",
        "status": {"type": "idle"},
        "createdAt": _BASE - index * 60_000,
        "updatedAt": _BASE - index * 60_000,
        "cwd": cwd,
        "repositoryRoot": cwd,
    }
    row.update(extra)
    return row


def listed(*rows: dict[str, object]) -> dict[str, object]:
    """A `session/list` answer holding `rows`."""
    return {
        "session/list": {
            "items": list(rows),
            "nextCursor": None,
            "previousCursor": None,
        }
    }
