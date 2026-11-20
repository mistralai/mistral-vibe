"""A todo list taller than its window scrolls with j and shows the scroll hint."""

from __future__ import annotations

from e2e.app_server.events import todo, todo_item, turn_started, user_msg
from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, handshake

__all__ = ["handshake"]

timeline: Timeline = [
    "plan it\r",
    turn_started(),
    user_msg("plan it"),
    todo([
        todo_item(str(index), f"Step number {index}", "pending") for index in range(14)
    ]),
    CTRL_X,
    "t",
    *["j"] * 20,
]

screen_contains = ("Step number 13", "↑↓/jk scroll")
screen_excludes = ("Step number 0 ",)
