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

screen_contains = {
    "rust": (
        "Main conversation",
        "Explore (Explore 1) [running] · 2.5k tokens",
        "› Main conversation",
    )
}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent(_TASK),
    child_session_updated(context_tokens=2_500),
    turn_completed(),
    "\x1b[B",  # Down — the caret is on the last line: focus the list on Main
    "\x1b[B",  # Down — highlight the child row
    "\x1b[A",  # Up — back to the Main row
    "\x1b[A",  # Up — focus returns to the input
    "\x1b[B",  # Down — focus the list again
    "\r",  # Enter on Main — no view change
]
