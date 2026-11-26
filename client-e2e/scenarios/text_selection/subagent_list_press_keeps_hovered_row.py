"""Pressing a hovered subagent row keeps the bar on it until the release opens it."""

from __future__ import annotations

from e2e.app_server.events import (
    child_session_updated,
    subagent,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot

_PROMPT = "delegate the investigation"
_HOVER_CHILD = "\x1b[<35;10;37M"
_PRESS_CHILD = "\x1b[<0;10;37M"
_RELEASE_CHILD = "\x1b[<0;10;37m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(context_tokens=2_500),
    turn_completed(),
    "\x18",  # Ctrl+X — focus the rail on its agents item
    "\r",  # Enter — open the subagent list
    _HOVER_CHILD,
    _PRESS_CHILD,
    _RELEASE_CHILD,
    take_snapshot(),
]
