"""Once its last child is stopped, the rail drops its subagents item and the list stays closed."""

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

screen_contains = ("The investigation was stopped.",)
screen_excludes = ("Main conversation", "Explore (Explore 1)", "subagent", "waiting")

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(),
    child_session_updated("archived"),
    assistant_msg("The investigation was stopped."),
    turn_completed(),
    "\x18",  # Ctrl+X — no rail item to focus: the prompt keeps the keys
    "\r",  # Enter — an empty prompt sends nothing
]
