"""A truncated stream silently retries, and the full reply replaces the discarded answer."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    discard_entry,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The retried turn's events ride the turn/start's on_request, as the replay's answer to it.
on_request = {
    "turn/start": [
        turn_started(),
        assistant_msg("I thought it through."),
        turn_completed(),
    ]
}

screen_contains = ("I thought it through.",)
screen_excludes = ("Let me think", "Stream ended early", "/retry")

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Let me think", entry_id="partial", generation_status="in_progress"),
    discard_entry("partial"),
    turn_failed(code="incomplete_stream", message="Stream ended early"),
]
