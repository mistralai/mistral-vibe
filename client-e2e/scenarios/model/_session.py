"""A config/write answer on the startup theme; the fixture's default answers a theme pick."""

from __future__ import annotations

from typing import Any


def session_handshake() -> dict[str, Any]:
    return {"config/write": {"runtime": {"config": {"theme": "ansi-dark"}}}}
