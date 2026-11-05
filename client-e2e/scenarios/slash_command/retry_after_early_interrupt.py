"""Esc before the turn starts: /retry still replaces the interrupt marker with the reply."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_interrupted,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

screen_contains = ("Here is the full answer.",)
screen_excludes = ("Interrupted", "/retry")

# The server starts the turn, adds its prompt, then closes it as the client's interrupt asks.
timeline: Timeline = [
    "hello\r",
    "\x1b",
    turn_started(),
    user_msg("hello"),
    turn_interrupted(),
    {"release": 4},
    "/retry\r",
    turn_started(),
    assistant_msg("Here is the full answer."),
    turn_completed(),
    {"release": 4},
]
