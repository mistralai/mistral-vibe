"""While a truncated stream silently retries, no error shows and the loader reads Retrying."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

screen_contains = ("partial recovered.",)
screen_excludes = ("Stream ended early", "/retry", "Retrying")

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("partial", generation_status="in_progress"),
    turn_failed(code="incomplete_stream", message="Stream ended early"),
    # Frame: the injected turn/start is in flight; Retrying, and no error row.
    {"release": 5},
    turn_started(),
    assistant_msg(" recovered."),
    turn_completed(),
    # Frame: the continuation merged into the interrupted answer.
    {"release": 4},
]
