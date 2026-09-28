"""A non-side-channel slash command submitted during a turn is refused, not run."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# The refusal only happens while a turn runs, so settle on the busy frame.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

# A release step must not consume a marker of its own, so settle key by key.
settle_per_key = True


timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/resume",
    "\r",
]
