"""A copy across the blank rows that keep the queue at the bottom skips them."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = "Working on it."

# Drag from the reply into the blank rows that hold the queued prompt at the bottom.
_SELECT = "\x1b[<0;3;11M\x1b[<32;120;18M\x1b[<0;120;18m"

capture_steps = {2, 3}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    _SELECT,
]
