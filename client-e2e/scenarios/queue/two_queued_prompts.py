"""Several queued prompts share one `Queued` header and stay packed together."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "third\r",
    take_snapshot(),
]
