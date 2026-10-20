"""After two silent continuations, a third truncated stream surfaces one error offering /retry."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_failed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# Every injected continuation is truncated again: requests.json holds exactly two turn/start.
on_request = {
    "turn/start": [
        turn_started(),
        turn_failed(code="incomplete_stream", message="Stream ended early"),
    ]
}

screen_contains = (
    "first",
    "Stream ended early",
    "Run /retry [additional instructions] to continue the interrupted response.",
)

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("first", generation_status="in_progress"),
    turn_failed(code="incomplete_stream", message="Stream ended early"),
]
