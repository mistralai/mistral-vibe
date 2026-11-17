"""`/goal edit` edits the objective and the budget; an invalid budget is refused in place."""

from __future__ import annotations

from e2e.app_server.events import goal, goal_updated
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with

_EDITED = goal(
    objective="Make the parity suite pass now", token_budget=200_000, revision=4
)

handshake = {**started_with(goal()), "goal/set": {"goal": _EDITED}}
on_request = {"goal/set": [goal_updated(_EDITED)]}

_BACKSPACE = "\x7f"

timeline: Timeline = [
    "/goal edit\r",
    " now",
    "\t",
    "abc",
    "\r",
    _BACKSPACE * 3 + "200k",
    "\r",
    take_snapshot(),
]

screen_contains = ("12.5K / 200K · 4m",)
