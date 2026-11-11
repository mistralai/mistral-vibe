"""Ctrl+R previews the newest match, Up/Down move between matches, Enter keeps it unsent."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.reverse_search._history import VOICE_OFF, sent

handshake = VOICE_OFF
_UP = "\x1b[A"
_DOWN = "\x1b[B"

timeline: Timeline = [
    *sent("run the tests", "deploy staging", "test the parser"),
    "\x12",
    "test",
    _UP,
    _UP,
    _DOWN,
    "\r",
    take_snapshot(),
]
