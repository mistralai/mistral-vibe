"""A server-driven compaction pulses while it runs, then settles to a done row."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    compaction_settled,
    compaction_started,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The compaction row only pulses while a turn runs, so settle on busy frames.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
# Each release must land on its own settled frame.
settle_per_key = True

screen_contains = ("Context compacted",)
screen_excludes = ("Compacting context",)
# The first release is the three events below plus the queue update the
# harness adds after `turn/started`; each later release is its own frame.
timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    compaction_started(),
    compaction_settled(),
    assistant_msg("Hi there."),
    turn_completed(),
    {"release": 4},
    {"release": 1},
    {"release": 2},
]
