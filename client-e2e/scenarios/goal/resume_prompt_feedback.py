"""With the feedback prompt and the resume choice both shown, `1` rates the turn and leaves the goal paused."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._resumed import (
    RESUMED_ID,
    client_args as client_args,
    handshake as _resumed,
    on_request as on_request,
)

handshake = {
    **_resumed,
    "feedback/record": {},
    "feedback/shouldShow": {"show": True, "snoozeDurationSeconds": 604800},
}

timeline: Timeline = [
    "hi\r",
    turn_started(RESUMED_ID),
    user_msg("hi", session_id=RESUMED_ID),
    assistant_msg("Hi. What do you need?", session_id=RESUMED_ID),
    turn_completed(session_id=RESUMED_ID),
    {"release": 3},
    "1",
    {"release": 2},
    take_snapshot(),
]

screen_contains = ("paused when the session closed",)
