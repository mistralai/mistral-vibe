"""A shell command submitted during a turn is refused, kept in the input, and never run."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
screen_contains = ("Shell commands cannot be queued", "! echo not queued")
screen_excludes = ("Queued",)
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "!echo not queued",
    "\r",
]
