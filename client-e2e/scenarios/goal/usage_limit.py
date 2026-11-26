"""A turn stopped for lack of credit explains it and offers no `/retry` hint."""

from __future__ import annotations

from e2e.app_server.events import (
    goal,
    goal_updated,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with

handshake = started_with(goal())

timeline: Timeline = [
    "keep going\r",
    turn_started(),
    user_msg("keep going"),
    goal_updated(goal("usage_limited")),
    turn_failed(code="usage_limit", message="Payment required"),
    take_snapshot(),
]

screen_contains = (
    "Your account has no credit left. Add credit, then run /goal resume or retry.",
    "goal usage limit",
)
screen_excludes = ("Run /retry",)
