"""Once shown, the subagent list keeps its Main row after its last child is stopped."""

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

_PROMPT = "delegate then stop the investigation"

screen_contains = ("Main conversation", "The investigation was stopped.")
screen_excludes = ("Explore (Explore 1)", "waiting")

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(),
    child_session_updated("archived"),
    assistant_msg("The investigation was stopped."),
    turn_completed(),
]
