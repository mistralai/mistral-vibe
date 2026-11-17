"""A rejected `/goal edit` reopens with what was typed, and an emptied objective is refused in the form."""

from __future__ import annotations

from e2e.app_server.events import goal
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with

handshake = {
    **started_with(goal()),
    "goal/set": {
        "error": {
            "code": "conflict",
            "message": "The goal changed. Reload it and try again.",
        }
    },
}

_BACKSPACE = "\x7f"

timeline: Timeline = [
    "/goal edit\r",
    " now",
    "\r",
    take_snapshot("rejected"),
    _BACKSPACE * 40,
    "\r",
    take_snapshot("empty"),
]

screen_contains = ("Edit goal", "Enter an objective.")
