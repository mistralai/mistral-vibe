"""Ctrl+C interrupts a running turn, as the loading hint says."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "write the migration"

# A partial answer is already shown, so the interrupt keeps the prompt and marks the turn.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg("Creating the", generation_status="in_progress"),
    "\x03",
]

# The turn is interrupted instead of arming the quit confirmation.
screen_contains = ("Interrupted",)
screen_excludes = ("Esc/Ctrl+C to interrupt", "again to quit")
