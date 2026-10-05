"""A paste while voice settings are open during a running turn is dropped; the panel stays usable."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    paste,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
screen_contains = {"rust": ("Generating", "Voice settings closed (no changes saved).")}
screen_excludes = {"rust": ("should not be pasted", "Voice Settings")}
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Done."),
    turn_completed(),
    turn_started(),
    {"release": 5},
    "/voice",
    "\r",
    {"release": 1},
    paste("should not be pasted"),
    "j",
    "\x1b",
]
