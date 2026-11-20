"""Replacing an unfinished goal asks first: Cancel keeps it, Replace sends `replace`."""

from __future__ import annotations

from e2e.app_server.events import goal, goal_updated
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import ESCAPE, started_with

_NEW = goal(
    objective="Write the docs", token_budget=None, tokens_used=0, time_used_seconds=0
)

handshake = {**started_with(goal()), "goal/set": {"goal": _NEW}}
on_request = {"goal/set": [goal_updated(_NEW)]}

_DOWN = "\x1b[B"

timeline: Timeline = [
    "/goal Write the docs\r",
    _DOWN,
    "\r",
    "/goal Write the docs\r",
    ESCAPE,
    "/goal Write the docs\r",
    "\r",
    take_snapshot(),
]

screen_contains = ("▶ goal 0s",)
