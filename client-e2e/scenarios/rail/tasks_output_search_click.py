"""A click on the output's search row focuses the field, so typing searches; a click on the text leaves it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

# While searching, the search row is row 38 and the text above it (1-based SGR coordinates).
_FIELD = "\x1b[<0;10;38M\x1b[<0;10;38m"
_TEXT = "\x1b[<0;10;30M\x1b[<0;10;30m"

timeline: Timeline = [
    *SETUP,
    CTRL_X,
    "b",
    DOWN,
    "\r",
    "/",
    "batch",
    ESCAPE,
    _FIELD,
    "-0",
    _TEXT,
]

screen_contains = ("batch-09.pdf", "9/9", "Esc clear search")
