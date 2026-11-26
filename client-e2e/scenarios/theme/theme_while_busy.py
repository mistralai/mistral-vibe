"""A theme picked during a running turn is written at once."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

screen_excludes = ("Slash commands cannot be queued",)
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    take_snapshot(),
    {"release": 4},
    take_snapshot(),
    "/theme",
    take_snapshot(),
    "\r",
    take_snapshot(),
    "\x1b[B",
    take_snapshot(),
    "\r",
    take_snapshot(),
]
