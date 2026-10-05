"""Ctrl+O folding a group read from its middle brings the group header to the top."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

screen_rows = {"rust": {0: "⏵ Ran commands"}}

_WHEEL_UP = "\x1b[<64;60;10M"
_CTRL_O = "\x0f"
_PROMPT = "run the checks"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    *(bash(f"check {index:02d}", f"ok {index:02d}") for index in range(1, 21)),
    assistant_msg("\n\n".join(f"Result {index:02d}" for index in range(1, 31))),
    turn_completed(),
    _CTRL_O,
    # The viewport top lands inside the expanded group.
    _WHEEL_UP * 32,
    _CTRL_O,
]
