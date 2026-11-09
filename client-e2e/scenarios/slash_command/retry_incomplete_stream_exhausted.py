"""After two silent retries, a third truncated stream surfaces one error offering /retry."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    discard_entry,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# Every injected retry is truncated again: requests.json holds exactly two turn/start.
on_request = {
    "turn/start": [
        turn_started(),
        turn_failed(code="incomplete_stream", message="Stream ended early"),
    ]
}

screen_contains = (
    "first",
    "Stream ended early",
    "Run /retry [additional instructions] to try again.",
)

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("first", entry_id="partial", generation_status="in_progress"),
    discard_entry("partial"),
    turn_failed(code="incomplete_stream", message="Stream ended early"),
]
