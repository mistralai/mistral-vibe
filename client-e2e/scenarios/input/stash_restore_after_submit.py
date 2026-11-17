"""Ctrl+S stashes the draft, and sending another prompt gives it back."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

_CTRL_S = "\x13"

screen_contains = ("> long draft to keep",)
screen_excludes = ("restore stash",)

timeline: Timeline = [
    "long draft to keep",
    _CTRL_S,
    take_snapshot("stashed"),
    "quick question\r",
    turn_started(),
    user_msg("quick question"),
    assistant_msg("Answered."),
    turn_completed(),
]
