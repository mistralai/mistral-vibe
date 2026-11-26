"""A live snapshot missing a row keeps the interrupt marker above the next prompt."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    live_snapshot,
    turn_interrupted,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

on_request = {"turn/interrupt": [turn_interrupted()]}

_first = user_msg("find the hung process", entry_id="first")
_cut_off = assistant_msg(
    "Let me sample its stack", entry_id="cut-off", generation_status="in_progress"
)
_next = user_msg("launch your own sub-process", entry_id="next")
_tool = bash("vibe --resume abc -p ping &", entry_id="tool")

# The snapshot a background process start sends, without the cut-off row.
timeline: Timeline = [
    "find the hung process\r",
    turn_started(),
    _first,
    _cut_off,
    {"release": 4},
    "\x1b",
    "launch your own sub-process\r",
    turn_started(),
    _next,
    _tool,
    live_snapshot(_first, _next, _tool),
    {"release": 5},
]

screen_contains = ("Interrupted",)
