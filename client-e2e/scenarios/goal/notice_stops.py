"""An interrupted turn pauses the goal and a later turn completes it; both stops show inline."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    entry_added,
    goal,
    goal_notice,
    goal_updated,
    turn_completed,
    turn_interrupted,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with

handshake = started_with(goal())

timeline: Timeline = [
    "fix the tests\r",
    turn_started(),
    user_msg("fix the tests"),
    assistant_msg("Looking at the failures."),
    turn_interrupted(),
    goal_updated(goal("paused", status_reason="interrupted")),
    entry_added(
        goal_notice(
            "interrupted",
            "Goal paused: turn interrupted",
            level="warning",
            status="paused",
        )
    ),
    "carry on\r",
    turn_started(),
    user_msg("carry on"),
    assistant_msg("All tests pass."),
    turn_completed(),
    goal_updated(goal("complete", tokens_used=40_000, time_used_seconds=4_320)),
    entry_added(
        goal_notice(
            "completed",
            "Goal complete: Make the parity suite pass",
            objective="Make the parity suite pass",
            status="complete",
            tokens_used=40_000,
            time_used_seconds=4_320,
        )
    ),
    take_snapshot(),
]

screen_contains = (
    "! Goal paused: turn interrupted",
    "✓ Goal complete · 40K tokens · 1h12m · Make the parity suite pass",
)
