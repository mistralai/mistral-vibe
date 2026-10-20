"""Esc during a silent continuation interrupts it, and the next prompt still streams."""

from __future__ import annotations

import copy

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline


def _turn_interrupted() -> dict:
    event = copy.deepcopy(turn_completed())
    event["params"]["turn"]["status"] = "interrupted"
    return event


env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

# The server answers the interrupt by closing the continuation turn.
on_request = {"turn/interrupt": [_turn_interrupted()]}

screen_contains = ("partial retry", "after cancel")
screen_excludes = ("Stream ended early", "/retry")

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("partial", generation_status="in_progress"),
    turn_failed(code="incomplete_stream", message="Stream ended early"),
    {"release": 5},  # the failure: the client injects the continuation turn/start
    turn_started(),
    assistant_msg(" retry", generation_status="in_progress"),
    {"release": 3},  # the continuation is streaming
    "\x1b",  # Esc — interrupt the continuation
    "next\r",
    turn_started(),
    user_msg("next"),
    assistant_msg("after cancel"),
    turn_completed(),
    {"release": 5},
]
