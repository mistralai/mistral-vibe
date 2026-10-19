"""An interrupted turn settles its unfinished compaction row instead of leaving it pulsing."""

from __future__ import annotations

from e2e.app_server.events import (
    compaction_started,
    turn_interrupted,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The compaction row only pulses while a turn runs, so settle on busy frames.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
# Each release must land on its own settled frame.
settle_per_key = True

screen_contains = ("Context compaction interrupted",)
screen_excludes = ("Compacting context",)
# The first release is the three events below plus the queue update the
# harness adds after `turn/started`; the interrupt then ends the turn with no
# terminal checkpoint, as the harness does for an interrupted compaction.
timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    compaction_started(),
    turn_interrupted(),
    {"release": 4},
    {"release": 1},
]
