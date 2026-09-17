"""Backspace removes the highlighted queued prompt through `queue/remove`."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    queued_turn,
    turn_queue_updated,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

# A removal only counts once the server says the item is gone, so answer the
# request with the queue update it would have emitted.
on_request = {"session/turn/queue/remove": [turn_queue_updated([queued_turn(2)])]}

_UP = "\x1b[A"
_BACKSPACE = "\x7f"

settle_per_key = True
capture_steps = {1, 2, 3, 4, 5}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "third\r",
    _UP,
    _BACKSPACE,
]
