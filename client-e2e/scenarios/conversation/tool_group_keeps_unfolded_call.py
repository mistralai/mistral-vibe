"""A lone call unfolded before a second call arrives stays unfolded inside the new group."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True

_PROMPT = "run two commands"
# The lone call's header row while the turn is still running.
_UNFOLD = "\x1b[<0;1;15M\x1b[<0;1;15m"

screen_contains = {"rust": ("⏷ Ran 2 commands", "⏷ Ran echo one", "⎢ ⎣ one")}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("echo one", "one"),
    bash("echo two", "two"),
    assistant_msg("Done."),
    turn_completed(),
    # Turn start, the harness's queue drain, the prompt, and the first call.
    {"release": 4},
    _UNFOLD,
    {"release": 3},
]
