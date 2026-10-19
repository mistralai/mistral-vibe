"""A queued prompt keeps the running title between completed turns."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
capture_startup = False
capture_steps = {1, 2, 3, 4}
expected_titles = (">> Vibe", ">> Vibe", ">> Vibe", "Vibe")

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("First reply."),
    turn_completed(),
    {"release": 3},
    "second\r",
    {"release": 2},
    turn_started(),
    user_msg("second"),
    assistant_msg("Second reply."),
    turn_completed(),
    {"release": 5},
]
