"""Esc on the subagent list closes it back to the rail; a second Esc returns the caret to the input."""

from __future__ import annotations

from e2e.app_server.events import (
    child_session_updated,
    subagent,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "delegate the investigation"

screen_contains = ("◐ 1 agent",)
screen_excludes = ("Main conversation", "s 1 agent")
screen_cursor = (35, 2)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(),
    turn_completed(),
    "\x18",  # Ctrl+X — focus the rail on its agents item
    "\r",  # Enter — open the list on the Main row
    "\x1b[B",  # Down — highlight the child row
    "\x1b",  # Esc — the list closes, the rail keeps focus
    "\x1b",  # Esc — focus returns to the input
]
