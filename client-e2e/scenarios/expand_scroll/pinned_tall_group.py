"""Expanding a group taller than the viewport while pinned brings its header to the top."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

screen_rows = {"rust": {1: "⏷ Ran commands"}}

_PROMPT = "run many commands"


def _click(row: int) -> str:
    return f"\x1b[<0;4;{row}M\x1b[<0;4;{row}m"


timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    *(bash(f"echo step {index:02d}", f"step {index:02d}") for index in range(1, 41)),
    assistant_msg("Done."),
    turn_completed(),
    # SGR row 30 hits the collapsed group header.
    _click(30),
]
