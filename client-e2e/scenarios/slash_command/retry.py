"""A retryable failure offers /retry; the retried reply replaces the unfinished answer and the error."""

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

# The server settles the cut-off answer as discarded before it fails the turn.
timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi the", entry_id="partial", generation_status="in_progress"),
    discard_entry("partial"),
    turn_failed(code="backend_error", message="Upstream 502"),
    "/retry\r",
    turn_started(),
    assistant_msg("Hi there."),
    turn_completed(),
]
