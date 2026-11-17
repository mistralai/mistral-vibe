"""A completed goal is cleared by the server: the open panel closes and the row goes, once."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    entry_added,
    goal,
    goal_notice,
    goal_updated,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with

handshake = started_with(goal())
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

_COMPLETE = goal("complete", tokens_used=40_000, time_used_seconds=4_320)

timeline: Timeline = [
    "finish it\r",
    turn_started(),
    user_msg("finish it"),
    assistant_msg("All done."),
    turn_completed(),
    goal_updated(_COMPLETE),
    entry_added(
        goal_notice(
            "completed",
            "Goal complete",
            status="complete",
            tokens_used=40_000,
            time_used_seconds=4_320,
        )
    ),
    goal_updated(None),
    # The turn, its queue drain, the completion and its notice; the removal waits.
    {"release_after": 7},
    "/goal\r",
    {"release": 1},
    "/goal\r",
    take_snapshot(),
]

screen_contains = ("No goal set. Use /goal <objective>.",)
screen_excludes = ("Goal cleared", "Goal Make the parity suite pass")
