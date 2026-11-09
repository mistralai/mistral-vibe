"""An overflowing subagent list shows a scrollbar in its last column."""

from __future__ import annotations

from e2e.app_server.events import (
    child_session_updated,
    subagent,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "delegate eight investigations"

screen_contains = ("Main conversation", "Explore (Explore 6) [running]")
screen_excludes = ("Explore (Explore 7)", "Explore (Explore 8)")

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    *(
        child_session_updated(
            child_session_id=f"child-session-{index}", name=f"explore-{index}"
        )
        for index in range(1, 9)
    ),
    turn_completed(),
]
