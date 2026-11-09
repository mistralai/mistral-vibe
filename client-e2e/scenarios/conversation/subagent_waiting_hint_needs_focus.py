"""The `a` hint on the waiting row shows only while the subagent list owns the keys."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    child_session_updated,
    subagent,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "delegate the investigation"

screen_contains = ("1 subagent waiting",)
screen_excludes = ("a to show",)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(),
    child_session_updated("idle"),
    assistant_msg("The investigation is done."),
    turn_completed(),
    "\x1b[B",  # Down — focus the list: the waiting row offers `a to show`
    "\x1b",  # Esc — back to the input: the hint goes away
]
