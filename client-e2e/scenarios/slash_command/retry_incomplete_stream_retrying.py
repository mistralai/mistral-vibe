"""While a truncated stream silently retries, no error shows and the loader reads Retrying."""

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

screen_contains = ("Recovered in full.",)
screen_excludes = ("partial", "Stream ended early", "/retry", "Retrying")

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("partial", entry_id="partial", generation_status="in_progress"),
    discard_entry("partial"),
    turn_failed(code="incomplete_stream", message="Stream ended early"),
    # Frame: the injected turn/start is in flight; Retrying, and no error row.
    {"release": 6},
    turn_started(),
    assistant_msg("Recovered in full."),
    turn_completed(),
    # Frame: the retried reply replaced the discarded answer.
    {"release": 4},
]
