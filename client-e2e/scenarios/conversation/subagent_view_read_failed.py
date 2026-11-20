"""A spawned child whose transcript the harness refuses to read shows the closed placeholder, never an endless loader."""

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
_CHILD_SESSION_ID = "child-session"

handshake = {
    f"session/read#{_CHILD_SESSION_ID}": {
        "error": {
            "code": "not_found",
            "message": f"Session not found: {_CHILD_SESSION_ID}",
        }
    }
}
request_methods = frozenset({"session/read"})

screen_contains = (
    "Viewing Explore 1 read-only",
    "This subagent is closed. Its transcript is no longer available.",
)
screen_excludes = ("Loading subagent transcript…",)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated("idle"),
    turn_completed(),
    "\x18",  # Ctrl+X — focus the rail on its agents item
    "\r",  # Enter — open the subagent list on the Main row
    "\x1b[B",  # Down — highlight the child row
    "\r",  # Enter — open the child view; its read fails
]
