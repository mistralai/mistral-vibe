"""A prompt submitted during a turn is queued under the `Queued` header."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# The queue is only visible while a turn runs, so settle on the busy frame.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

# One batch only: the second enqueue has nothing left to release, so its prompt
# stays queued instead of starting a turn. The four released events are the
# three below plus the queue update the harness adds after `turn/started`.
# A release step must not consume a marker of its own, so settle key by key.
settle_per_key = True
capture_steps = {1, 2}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
]
