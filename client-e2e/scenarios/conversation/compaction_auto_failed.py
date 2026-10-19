"""A failed server-driven compaction settles its running row to an error row."""

from __future__ import annotations

from e2e.app_server.events import (
    compaction_settled,
    compaction_started,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The compaction row only pulses while a turn runs, so settle on busy frames.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
# Each release must land on its own settled frame.
settle_per_key = True

screen_contains = ("Context compaction failed",)
screen_excludes = ("Compacting context",)
# The first release is the three events below plus the queue update the
# harness adds after `turn/started`; the failure then settles the turn.
timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    compaction_started(),
    compaction_settled(failed=True),
    turn_failed("context_compaction_failed", "The compaction summary was empty."),
    {"release": 4},
    {"release": 2},
]
