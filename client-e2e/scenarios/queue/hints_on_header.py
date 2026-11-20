"""The queue's keys sit on its `» Queued` header; the loading line keeps only the interrupt."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
]

screen_contains = (
    "» Queued   Enter steer  Ctrl+C cancel last queued message",
    "Esc to interrupt)",
)
screen_excludes = ("Enter to steer", "to cancel last queued")
