"""Ctrl+C over a picker opened during a turn arms quit, keeping the turn and the queue."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
screen_contains = ("Queued", "second", "again to quit", "Generating")
screen_excludes = ("Interrupted",)
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "/theme",
    "\r",
    "\x03",
]
