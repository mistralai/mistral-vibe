"""Clicking a subagent list row collapses the composer selection to its caret."""

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
_SHIFT_LEFT = "\x1b[1;2D"
_CLICK_MAIN = "\x1b[<0;5;38M\x1b[<0;5;38m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(context_tokens=2_500),
    turn_completed(),
    "draft text",
    _SHIFT_LEFT * 4,
    _CLICK_MAIN,
    "\x1b[A",  # Up on Main: focus returns to the input
    "X",
]
capture_steps = {2, 3, 4, 5}

# A surviving selection would be replaced by the typed key: "draft X".
screen_contains = {"rust": ("draft Xtext",)}
