"""Down leaves history mode after restoring the live multiline draft."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_UP = "\x1b[A"
_DOWN = "\x1b[B"

timeline: Timeline = [
    "older\r",
    turn_started(),
    user_msg("older"),
    assistant_msg("first"),
    turn_completed(),
    "newer\r",
    turn_started(),
    user_msg("newer"),
    assistant_msg("second"),
    turn_completed(),
    "top\nbottom",
    _UP,
    _UP,
    _UP,
    _UP,
    _DOWN,
    _DOWN,
    _DOWN,
    "X",
]

capture_steps = {8, 9, 10}
