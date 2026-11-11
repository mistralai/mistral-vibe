"""Enter loads the highlighted prompt into the input; Enter again saves the edit."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

_UP = "\x1b[A"

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    _UP,
    "\r",
    " edited\r",
    take_snapshot(),
]
