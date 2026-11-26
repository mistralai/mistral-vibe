"""Shared goal handshakes: a session that already has a goal."""

from __future__ import annotations

from typing import Any

ESCAPE = "\x1b[27u"


def started_with(value: dict[str, Any]) -> dict[str, Any]:
    """A handshake whose started session already carries `value` as its goal."""
    return {"session/start": {"state": {"session": {"goal": value}}}}
