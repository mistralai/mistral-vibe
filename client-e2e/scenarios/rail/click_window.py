"""A click opens an item's window with its button lit; Right walks to the next window and Esc returns to the prompt."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

# The todo item sits at the end of the rail row 34 (1-based SGR coordinates).
_HOVER_TODO = "\x1b[<35;116;34M"
_CLICK_TODO = "\x1b[<0;116;34M\x1b[<0;116;34m"

timeline: Timeline = [
    *SETUP,
    CTRL_X,
    _HOVER_TODO,
    _CLICK_TODO,
    "\x1b[C",
    take_snapshot("opened"),
    ESCAPE,
]

screen_contains = (
    "Esc/Ctrl+C to interrupt",
    "◐ 1 agent · ▪ 2 tasks · ▶ goal 4m · ≡ 1/3",
)
screen_excludes = ("Todo 1/3", "t 1/3")
