"""A truncated stream silently continues instead of surfacing a manual retry."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# An incomplete_stream failure never mounts an error row: the client silently
# re-sends an injected turn/start (Python `_auto_retry_incomplete_stream`)
# whose continuation appends to the interrupted answer's row. The retried
# turn's events ride the turn/start's on_request, as the replay's answer to it.
on_request = {
    "turn/start": [turn_started(), assistant_msg(" this through."), turn_completed()]
}

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Let me think", generation_status="in_progress"),
    turn_failed(code="incomplete_stream", message="Stream ended early"),
]
