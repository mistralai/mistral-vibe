from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    scheduled_loop_fired,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    scheduled_loop_fired("Run the linter", "loop-1"),
    assistant_msg("Hello."),
    turn_completed(),
]
