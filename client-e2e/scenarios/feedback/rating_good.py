"""Show feedback beside active loading and consume the good-rating key."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

handshake = {
    "feedback/record": {},
    "feedback/shouldShow": {"show": True, "snoozeDurationSeconds": 604800},
}
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
capture_steps = {1, 2, 3}
settle_per_key = True

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    {"release": 3},
    "1",
    {"release": 2},
]
