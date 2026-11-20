"""A narrow Ctrl+R search wraps its match hints under the query instead of cutting Esc cancel off."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize, take_snapshot
from scenarios.reverse_search._history import VOICE_OFF, sent

handshake = VOICE_OFF

timeline: Timeline = [
    *sent("run the tests"),
    resize(30, 44),
    "\x12",
    "tests",
    take_snapshot(),
]

screen_contains = ("reverse-i-search: tests", "Enter accept", "Esc cancel")
