"""Tab and Shift+Tab walk the rail windows like ←→, from the subagent list too, wrapping at both ends."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

_TAB = "\t"
_SHIFT_TAB = "\x1b[Z"

timeline: Timeline = [
    *SETUP,
    CTRL_X,
    "s",
    _TAB,
    take_snapshot("tasks"),
    _SHIFT_TAB,
    _SHIFT_TAB,
]

screen_contains = ("Todo 1/3", "Run the parity suite")
screen_excludes = ("Main conversation", "Background tasks")
