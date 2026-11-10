"""Ctrl+C over a picker opened during a turn arms quit, keeping the turn and the queue."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

screen_contains = ("Queued", "second", "again to quit", "Generating")
screen_excludes = ("Interrupted",)
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    take_snapshot(),
    {"release": 4},
    take_snapshot(),
    "second\r",
    take_snapshot(),
    "/theme",
    take_snapshot(),
    "\r",
    take_snapshot(),
    "\x03",
    take_snapshot(),
]
