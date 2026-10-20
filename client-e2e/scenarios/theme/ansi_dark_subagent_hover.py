"""ANSI-dark subagent row under the mouse keeps its readable cursor bar."""

from __future__ import annotations

from e2e.app_server.events import (
    child_session_updated,
    subagent,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_THEME": "ansi-dark"}
handshake = {
    "config/read": {"config": {"theme": "ansi-dark"}},
    "runtime/read": {"runtime": {"config": {"theme": "ansi-dark"}}},
}
_PROMPT = "delegate the investigation"
_HOVER_CHILD = "\x1b[<35;10;39M"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(context_tokens=2_500),
    turn_completed(),
    _HOVER_CHILD,
]
