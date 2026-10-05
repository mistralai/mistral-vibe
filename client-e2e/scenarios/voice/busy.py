"""Voice settings are idle-only and preserve a command submitted while busy."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
screen_contains = {"rust": ("Slash commands cannot be queued", "/ voice")}
screen_excludes = {"rust": ("Voice Settings", "not implemented")}
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/voice",
    "\r",
]
