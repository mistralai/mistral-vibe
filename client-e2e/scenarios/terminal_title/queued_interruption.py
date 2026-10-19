"""An interrupted turn keeps queued work active until the server pauses it."""

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
settle_per_key = True
capture_startup = False
capture_steps = {1, 2, 3, 4, 5}
expected_titles = (">> Vibe", ">> Vibe", ">> Vibe", ">> Vibe", "Vibe")

# app_server/_turns.py::_after_turn_terminal pauses after turn/completed.
_interrupted = turn_completed()
_interrupted["params"]["turn"]["status"] = "interrupted"

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    _interrupted,
    turn_queue_updated([queued_turn(2, "second")], paused=True),
    {"release": 4},
    "second\r",
    "\x1b",
    {"release": 1},
    {"release": 1},
]
