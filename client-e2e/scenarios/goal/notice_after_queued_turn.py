"""A stop notice recorded after the next turn started still follows the turn that stopped the goal."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    entry_added,
    goal,
    goal_notice,
    goal_updated,
    in_turn,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import AppServerEvent, Timeline, take_snapshot

_CONTINUATION = "turn-goal-continuation-1"
_OBJECTIVE = "Make the parity suite pass"


def _unqueued(event: AppServerEvent) -> AppServerEvent:
    event = in_turn(event, _CONTINUATION)
    event["params"]["turn"]["queueItemId"] = None
    return event


def _completed_notice() -> AppServerEvent:
    notice = goal_notice(
        "completed",
        "Goal complete",
        status="complete",
        tokens_used=40_000,
        time_used_seconds=4_320,
    )
    notice["turnId"] = _CONTINUATION
    return entry_added(notice)


handshake = {"goal/set": {"goal": goal()}}
on_request = {
    "goal/set": [
        goal_updated(goal()),
        _unqueued(turn_started()),
        in_turn(assistant_msg("The suite passes; the goal is done."), _CONTINUATION),
        _unqueued(turn_completed()),
    ]
}

timeline: Timeline = [
    f"/goal {_OBJECTIVE}\r",
    "and the docs?\r",
    turn_started(),
    user_msg("and the docs?"),
    assistant_msg("The docs are up to date."),
    goal_updated(goal("complete", tokens_used=40_000, time_used_seconds=4_320)),
    # The continuation's stop lands only once the queued turn already answered.
    _completed_notice(),
    turn_completed(),
    take_snapshot(),
]

screen_contains = ("✓ Goal complete · 40K tokens · 1h12m",)
