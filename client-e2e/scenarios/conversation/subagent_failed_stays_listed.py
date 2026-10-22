"""A failed subagent stays listed once its batch ends; only a stopped one leaves."""

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

_PROMPT = "delegate three investigations"
_FAILED = {"child_session_id": "child-session-2", "name": "explore-2"}
_STOPPED = {"child_session_id": "child-session-3", "name": "explore-3"}

screen_contains = (
    "Explore (Explore 1) [ready]",
    "Explore (Explore 2) [failed]",
    "One investigation failed.",
)
screen_excludes = ("Explore 3",)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(),
    child_session_updated(**_FAILED),
    child_session_updated(**_STOPPED),
    child_session_updated("idle"),
    child_session_updated("archived", **_STOPPED),
    child_session_updated("failed", **_FAILED),
    assistant_msg("One investigation failed."),
    turn_completed(),
]
