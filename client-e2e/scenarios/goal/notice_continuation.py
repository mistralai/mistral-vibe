"""After `/log`, setting a goal shows its notice, and the continuation's notice heads its output."""

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
)
from e2e.app_server.scenario import AppServerEvent, Timeline, take_snapshot

_TURN = "turn-goal-continuation-1"
_OBJECTIVE = "Make the parity suite pass"


def _continuation() -> list[AppServerEvent]:
    started = in_turn(turn_started(), _TURN)
    started["params"]["turn"]["queueItemId"] = None
    completed = in_turn(turn_completed(), _TURN)
    completed["params"]["turn"]["queueItemId"] = None
    notice = goal_notice("continued", "Working on the goal", continuation=1)
    notice["turnId"] = _TURN
    return [
        started,
        # The notice is recorded after the turn starts, so streamed output can precede it.
        in_turn(assistant_msg("Running the parity suite now."), _TURN),
        entry_added(notice),
        in_turn(assistant_msg("Two tests still fail; fixing them."), _TURN),
        completed,
    ]


handshake = {
    "goal/set": {"goal": goal()},
    "session/log/read": {
        "log": {
            "enabled": True,
            "sessionId": "00000000-0000-4000-8000-000000000001",
            "persisted": True,
            "path": "/test/logs/session",
            "title": None,
            "needsInitialAutoTitle": True,
        }
    },
}
on_request = {
    "goal/set": [
        goal_updated(goal()),
        entry_added(
            goal_notice(
                "set", f"Goal set: {_OBJECTIVE}", objective=_OBJECTIVE, tokens_used=0
            )
        ),
        *_continuation(),
    ]
}

timeline: Timeline = ["/log\r", f"/goal {_OBJECTIVE}\r", take_snapshot()]

screen_contains = (
    f"◆ Goal set · {_OBJECTIVE}",
    "◆ Working on the goal · continuation 1 · 12.5K / 50K tokens",
)
