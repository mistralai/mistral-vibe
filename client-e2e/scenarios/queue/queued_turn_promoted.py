"""A queued prompt keeps its place once promoted: prompt, reply, prompt, reply."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

settle_per_key = True
capture_steps = {1, 2, 3, 4}

# Each turn releases five events: start, the queue update the harness adds, the
# user echo, the reply, and completion.
timeline: Timeline = [
    "say hi\r",
    turn_started(),
    user_msg("say hi"),
    assistant_msg("Hi. What are we working on?"),
    turn_completed(),
    {"release": 3},
    "say ho\r",
    {"release": 2},
    turn_started(),
    user_msg("say ho"),
    assistant_msg("Ho. Ready when you are."),
    turn_completed(),
    {"release": 5},
]
