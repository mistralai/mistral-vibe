"""`/goal <objective>` pins a goal row above the todo row; each status renders its own right part."""

from __future__ import annotations

from typing import Any

from e2e.app_server.events import (
    goal,
    goal_updated,
    todo,
    todo_item,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot

handshake = {"goal/set": {"goal": goal()}}
on_request = {"goal/set": [goal_updated(goal())]}


def _turn(prompt: str, value: dict[str, Any]) -> Timeline:
    return [
        f"{prompt}\r",
        turn_started(),
        user_msg(prompt),
        goal_updated(value),
        turn_completed(),
    ]


timeline: Timeline = [
    "/goal Make the parity suite pass\r",
    "plan it\r",
    turn_started(),
    user_msg("plan it"),
    todo([todo_item("1", "Run the parity suite", "in_progress")]),
    goal_updated(goal(token_budget=None, tokens_used=31_200, time_used_seconds=3_900)),
    turn_completed(),
    *_turn("pause", goal("paused", status_reason="user")),
    *_turn("block", goal("blocked", status_reason="no_progress")),
    *_turn("budget", goal("budget_limited", tokens_used=50_000)),
    *_turn("credit", goal("usage_limited")),
    *_turn("finish", goal("complete", tokens_used=40_000, time_used_seconds=4_320)),
    take_snapshot(),
]

screen_contains = ("complete · 40K · 1h12m", "Run the parity suite")
