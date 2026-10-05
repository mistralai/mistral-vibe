"""Ctrl+C before the server starts the first turn interrupts that turn once it starts."""

from __future__ import annotations

from e2e.app_server.events import turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
capture_startup = False

_PROMPT = "write the migration"

# The server only starts the turn after the key press; the client then sends `turn/interrupt`.
timeline: Timeline = [
    f"{_PROMPT}\r",
    "\x03",
    turn_started(),
    user_msg(_PROMPT),
    {"release": 3},
]

screen_contains = {"rust": ("Interrupted",)}
screen_excludes = {"rust": ("Esc/Ctrl+C to interrupt",)}
