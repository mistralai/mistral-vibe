"""A refusal notice above the input stays until the next key, which clears it."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

settle_per_key = True
screen_contains = ("! xecho not queued",)
screen_excludes = ("Shell commands cannot be queued",)
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "!echo not queued",
    "\r",
    "x",
]
