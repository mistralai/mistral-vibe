"""Super+Enter on an empty input steers the accepted queued prompt into the active turn."""

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
_STEERED = user_msg("second")
_STEERED["params"]["entry"]["id"] = "queued-2"
_STEERED["params"]["entry"]["source"] = "turn_steer"

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
handshake = {
    "session/start": {"state": {"session": {"harness": "unified"}}},
    "session/turn/queue/steer": {
        "lastEventId": 5,
        "queueItemId": _SECOND_QUEUE_ITEM_ID,
        "turnId": TURN_ID,
    },
}
on_request = {"session/turn/queue/steer": [_STEERED]}

settle_per_key = True
capture_steps = {1, 2}
screen_contains = ("> second",)
screen_excludes = ("Queued",)

# CSI 13;9u is Super+Enter in the kitty keyboard protocol.
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    turn_queue_updated([queued_turn(2, "second")]),
    {"release": 5},
    "\x1b[13;9u",
]
