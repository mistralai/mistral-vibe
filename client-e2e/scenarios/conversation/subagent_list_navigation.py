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
_TASK = "find the grouping code"

screen_contains = (
    "Main conversation",
    "Explore (Explore 1) [running] · 2.5k tokens",
    "› Main conversation",
)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent(_TASK),
    child_session_updated(context_tokens=2_500),
    turn_completed(),
    "\x18",  # Ctrl+X — focus the rail on its agents item
    "\r",  # Enter — open the list on Main
    "\x1b[B",  # Down — highlight the child row
    "\x1b[A",  # Up — back to the Main row
    "\x1b[A",  # Up — the list closes back to the rail
    "\r",  # Enter — open the list again
    "\r",  # Enter on Main — no view change
]
