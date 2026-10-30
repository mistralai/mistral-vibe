"""A failed subagent stays listed once its batch ends; a waiting one hides and a stopped one leaves."""

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
_FAILED_ID, _FAILED_NAME = "child-session-2", "explore-2"
_STOPPED_ID, _STOPPED_NAME = "child-session-3", "explore-3"

screen_contains = (
    "Explore (Explore 2) [failed]",
    "1 subagent waiting",
    "One investigation failed.",
)
screen_excludes = ("Explore (Explore 1)", "Explore 3", "a to show")

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(),
    child_session_updated(child_session_id=_FAILED_ID, name=_FAILED_NAME),
    child_session_updated(child_session_id=_STOPPED_ID, name=_STOPPED_NAME),
    child_session_updated("idle"),
    child_session_updated("archived", child_session_id=_STOPPED_ID, name=_STOPPED_NAME),
    child_session_updated("failed", child_session_id=_FAILED_ID, name=_FAILED_NAME),
    assistant_msg("One investigation failed."),
    turn_completed(),
]
