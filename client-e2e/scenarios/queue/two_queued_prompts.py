"""Several queued prompts share one `Queued` header and stay packed together."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

# A release step must not consume a marker of its own, so settle key by key.
settle_per_key = True
capture_steps = {1, 2, 3}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "third\r",
]
