"""Saving a queued edit leaves queue mode, so an empty Enter steers the turn."""

from __future__ import annotations

from e2e.app_server.events import (
    QUEUE_ITEM_ID,
    TURN_ID,
    assistant_msg,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_SECOND_QUEUE_ITEM_ID = f"{QUEUE_ITEM_ID}-2"

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
handshake = {
    "session/turn/queue/steer": {
        "lastEventId": 5,
        "queueItemId": _SECOND_QUEUE_ITEM_ID,
        "turnId": TURN_ID,
    }
}

_UP = "\x1b[A"

# After the save the queue controls are gone and the edited prompt is still
# visible; the steer itself is proven by the requests.json golden.
screen_contains = {"rust": ("second edited",)}
screen_excludes = {"rust": ("↑↓/jk to select", "Enter to save")}
settle_per_key = True
capture_steps = {5, 6}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    _UP,
    "\r",
    " edited\r",
    "\r",
]
