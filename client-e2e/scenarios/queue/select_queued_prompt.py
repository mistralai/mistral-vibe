"""Up opens queue selection on the newest prompt, then walks towards the oldest."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

_UP = "\x1b[A"

# Each key must land on a settled UI: the highlight moves one prompt per press.
settle_per_key = True
capture_steps = {1, 2, 3, 4, 5}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "third\r",
    _UP,
    _UP,
]
