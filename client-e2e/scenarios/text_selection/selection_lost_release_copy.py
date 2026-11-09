"""A drag whose release is never reported still copies once the pointer moves again."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}

_PRESS = "\x1b[<0;1;8M"
_DRAG = "\x1b[<32;120;16M"
# The terminal dropped the release (a pixel report past the window edge); motion follows.
_MOVED = "\x1b[<35;60;12M"

clipboard_contains = (
    "copy without a release",
    "First line of the reply.",
    "Second line of the reply.",
)

timeline: Timeline = [
    "copy without a release\r",
    turn_started(),
    user_msg("copy without a release"),
    assistant_msg("First line of the reply.\n\nSecond line of the reply."),
    turn_completed(),
    _PRESS + _DRAG,
    _MOVED,
]
