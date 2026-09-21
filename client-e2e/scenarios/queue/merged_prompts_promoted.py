"""Promoting merged queued prompts preserves each prompt's displayed text."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

settle_per_key = True
capture_steps = {5}

timeline: Timeline = [
    "sleep for 10 s\r",
    turn_started(),
    user_msg("sleep for 10 s"),
    {"release": 3},
    "message 1\r",
    turn_started(),
    user_msg("message 1\n\nmessage 22\n\nmessage 3"),
    assistant_msg("Received all three messages."),
    "message 22\r",
    "message 3\r",
    {"release": 3},
]
