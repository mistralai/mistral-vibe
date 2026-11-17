"""After an interrupt, queue selection removes an older held prompt, not only the newest."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    queued_turn,
    turn_interrupted,
    turn_queue_updated,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

held = [queued_turn(2, "second\n\nthird")]
on_request = {
    "turn/interrupt": [turn_interrupted(), turn_queue_updated(held, paused=True)]
}

_UP = "\x1b[A"
_ESC = "\x1b"
_BACKSPACE = "\x7f"

screen_contains = ("third", "Backspace/Ctrl+C to remove")
screen_excludes = ("second",)

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "third\r",
    _ESC,
    _UP,
    _UP,
    _BACKSPACE,
]
