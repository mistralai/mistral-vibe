"""Running subagents list above ready ones, so an overflowing list never scrolls them out of view."""

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

screen_contains = (
    "Explore (Explore 7) [running]",
    "Explore (Explore 8) [running]",
    "Explore (Explore 1) [ready]",
)
screen_excludes = ("Explore (Explore 5)", "Explore (Explore 6)")

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
    *(
        child_session_updated(
            "idle", child_session_id=f"child-session-{index}", name=f"explore-{index}"
        )
        for index in range(1, 7)
    ),
    turn_completed(),
    "\x18",  # Ctrl+X — focus the rail on its agents item
    "\r",  # Enter — open the subagent list
]
