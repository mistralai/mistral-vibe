from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# A rate-limited turn shows the friendly Python `_rate_limit_message` text with
# the /retry hint, not the raw server message; the retry then continues the
# interrupted answer's row.
timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi the", generation_status="in_progress"),
    turn_failed(code="rate_limit", message="Too many requests"),
    "/retry\r",
    turn_started(),
    assistant_msg("Hi there."),
    turn_completed(),
]
