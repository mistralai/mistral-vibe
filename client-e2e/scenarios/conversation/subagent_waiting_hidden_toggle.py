"""Waiting subagents leave the list once their batch ends; `a` on the focused list shows them again."""

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

_PROMPT = "delegate two investigations"
_SECOND_ID, _SECOND_NAME = "child-session-2", "explore-2"

screen_contains = (
    "Explore (Explore 1) [ready]",
    "Explore (Explore 2) [ready]",
    "2 subagents waiting · a to hide",
)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(),
    child_session_updated(child_session_id=_SECOND_ID, name=_SECOND_NAME),
    child_session_updated("idle"),
    child_session_updated("idle", child_session_id=_SECOND_ID, name=_SECOND_NAME),
    assistant_msg("Both investigations are done."),
    turn_completed(),
    "\x1b[B",  # Down — focus the list on the Main row
    "a",  # a — show the hidden waiting subagents
]
