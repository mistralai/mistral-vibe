"""A consumed queued edit uses a visible Rust warning confirmation."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

_UP = "\x1b[A"
_WARNING = "This message was already processed"

screen_contains = {"rust": (_WARNING,)}
capture_steps = {10}
settle_per_key = True

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    turn_completed(),
    {"release": 4},
    "second\r",
    turn_started(),
    user_msg("second"),
    assistant_msg("Processing second."),
    "third\r",
    _UP,
    _UP,
    "\r",
    " edited",
    {"release": 1},
    {"release": 2},
    "\r",
]
