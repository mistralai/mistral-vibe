"""/retry after a completed answer warns that there is nothing to retry and sends nothing."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

screen_contains = ("Hi there.", "⚠ No failed or interrupted response to retry")
screen_excludes = ("/retry",)

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi there."),
    turn_completed(),
    "/retry\r",
]
