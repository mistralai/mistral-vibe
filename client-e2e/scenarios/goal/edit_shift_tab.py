"""Shift+Tab in `/goal edit` moves to the budget field and leaves the approval policy unchanged."""

from __future__ import annotations

from e2e.app_server.events import goal
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import ESCAPE, started_with

handshake = started_with(goal())

_SHIFT_TAB = "\x1b[Z"

timeline: Timeline = [
    "/goal edit\r",
    _SHIFT_TAB,
    "200k",
    take_snapshot("budget"),
    ESCAPE,
    take_snapshot("closed"),
]

screen_contains = ("accept edits",)
