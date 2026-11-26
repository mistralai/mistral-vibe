"""While a turn runs, `/sessions` opens the page, but opening another session is refused."""

from __future__ import annotations

from e2e.app_server.events import turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "work\r",
    turn_started(),
    user_msg("work"),
    "/sessions",
    "\r",
    "\x1b[A",
    "\r",
    take_snapshot(),
]
request_methods = {"session/resume"}
screen_contains = ("Wait for the current turn to finish",)
