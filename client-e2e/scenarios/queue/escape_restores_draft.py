"""Escape leaves queue selection and puts the untouched draft back in the input."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

_UP = "\x1b[A"
_ESCAPE = "\x1b[27u"

settle_per_key = True
capture_steps = {1, 2, 3, 4, 5, 6}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "hi",
    # The first Up parks the caret to protect the draft; the second opens the queue.
    _UP,
    _UP,
    _ESCAPE,
]
