"""A held queue says how to release it, and Enter releases it."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_queue_updated,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

settle_per_key = True
capture_steps = {1, 2, 3, 4}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    turn_queue_updated(paused=True),
    {"release": 1},
    "\r",
]
