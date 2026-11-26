"""A complete goal is replaced without asking."""

from __future__ import annotations

from e2e.app_server.events import goal, goal_updated
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with

_NEW = goal(
    objective="Write the docs", token_budget=None, tokens_used=0, time_used_seconds=0
)

handshake = {**started_with(goal("complete")), "goal/set": {"goal": _NEW}}
on_request = {"goal/set": [goal_updated(_NEW)]}

timeline: Timeline = ["/goal Write the docs\r", take_snapshot()]

screen_excludes = ("Replace the current goal?",)
