"""A running turn's reply renders above the queued prompts, not below them."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

settle_per_key = True
capture_steps = {1, 2, 3}

# The first release stops before the reply: the prompt is queued while the turn
# is still streaming, then the reply arrives and must slot in above the queue.
timeline: Timeline = [
    "say hi\r",
    turn_started(),
    user_msg("say hi"),
    assistant_msg("Hi. What are we working on?"),
    {"release": 3},
    "say ho\r",
    {"release": 1},
]
