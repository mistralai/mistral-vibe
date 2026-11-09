"""Esc on the focused subagent list returns focus to the input with Main conversation selected."""

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

screen_contains = ("› Main conversation", "Explore (Explore 1) [running]")
screen_cursor = (33, 2)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(),
    turn_completed(),
    "\x1b[B",  # Down — focus the list on the Main row
    "\x1b[B",  # Down — highlight the child row
    "\x1b",  # Esc — focus returns to the input, Main selected
]
