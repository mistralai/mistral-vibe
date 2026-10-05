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

# `show_subagent_status_list: false` hides the whole feature.
handshake = {"runtime/read": {"runtime": {"config": {"showSubagentStatusList": False}}}}

screen_excludes = {"rust": ("Main conversation", "Explore (Explore 1)")}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent(_TASK),
    child_session_updated(),
    turn_completed(),
    "\x1b[B",  # Down — no list to focus; the composer keeps focus
]
