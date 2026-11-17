"""After an interrupt, Up edits a held prompt in place and the queue stays held until Enter."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    queued_turn,
    turn_interrupted,
    turn_queue_updated,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot

# The server ends the turn and holds the prompt queued behind it.
on_request = {
    "turn/interrupt": [
        turn_interrupted(),
        turn_queue_updated([queued_turn(2, "second")], paused=True),
    ]
}

_UP = "\x1b[A"
_ESC = "\x1b"

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    _ESC,
    _UP,
    "\r",
    " edited\r",
    take_snapshot(),
]
