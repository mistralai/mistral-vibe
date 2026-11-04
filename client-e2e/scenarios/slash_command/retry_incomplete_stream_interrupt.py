"""Esc during a silent retry interrupts it, and the next prompt still streams."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    discard_entry,
    turn_completed,
    turn_failed,
    turn_interrupted,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The server answers the interrupt by discarding the cut-off reply and closing the turn.
on_request = {
    "turn/interrupt": [discard_entry("retried", cause="interrupt"), turn_interrupted()]
}

screen_contains = ("Recovering", "Interrupted", "after cancel")
screen_excludes = ("partial", "Stream ended early", "/retry")

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("partial", entry_id="partial", generation_status="in_progress"),
    discard_entry("partial"),
    turn_failed(code="incomplete_stream", message="Stream ended early"),
    {"release": 6},  # the failure: the client injects the retry turn/start
    turn_started(),
    assistant_msg("Recovering", entry_id="retried", generation_status="in_progress"),
    {"release": 3},  # the retry is streaming
    "\x1b",  # Esc interrupts the retry
    "next\r",
    turn_started(),
    user_msg("next"),
    assistant_msg("after cancel"),
    turn_completed(),
    {"release": 5},
]
