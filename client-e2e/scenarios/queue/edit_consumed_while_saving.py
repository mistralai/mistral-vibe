"""An accepted queued edit survives immediate promotion."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    queued_turn,
    turn_completed,
    turn_queue_updated,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

_REPLACE = "session/turn/queue/replace"
_UP = "\x1b[A"

_promoted = turn_started()
_promoted["params"]["turn"]["queueItemId"] = queued_turn(2)["id"]

on_request = {_REPLACE: [_promoted, turn_queue_updated()]}
screen_contains = {"python": ("second edited",), "rust": ("second edited",)}
capture_steps = {7}
settle_per_key = True

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    turn_completed(),
    {"release": 4},
    "second\r",
    _UP,
    "\r",
    " edited",
    {"release": 1},
    "\r",
]
