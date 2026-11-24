"""Once the search is cleared and its row hidden, a click where the row was selects text instead of reopening the search."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

# The last text row is row 38 (1-based SGR coordinates), where the search row sat.
_CLICK = "\x1b[<0;10;38M\x1b[<0;10;38m"

timeline: Timeline = [
    *SETUP,
    CTRL_X,
    "b",
    DOWN,
    "\r",
    "/",
    "batch",
    ESCAPE,
    ESCAPE,
    _CLICK,
]

screen_contains = ("progress 100%", "/ search")
screen_excludes = ("Search output", "Esc leave search")
