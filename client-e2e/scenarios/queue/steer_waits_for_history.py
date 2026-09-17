"""A successful queue-steer response stays pending until history confirms it."""

from __future__ import annotations

from e2e.app_server.events import (
    QUEUE_ITEM_ID,
    TURN_ID,
    assistant_msg,
    queued_turn,
    turn_queue_updated,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_SECOND_QUEUE_ITEM_ID = f"{QUEUE_ITEM_ID}-2"

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
handshake = {
    "session/start": {"state": {"session": {"harness": "unified"}}},
    "session/turn/queue/steer": {
        "lastEventId": 5,
        "queueItemId": _SECOND_QUEUE_ITEM_ID,
        "turnId": TURN_ID,
    },
}

settle_per_key = True
capture_steps = {1, 2}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    turn_queue_updated([queued_turn(2, "second")]),
    {"release": 5},
    "\r",
]
