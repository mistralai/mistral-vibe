"""A rate-limited turn shows the friendly limit text with the /retry hint; the retry replaces it."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    discard_entry,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi the", entry_id="partial", generation_status="in_progress"),
    discard_entry("partial"),
    turn_failed(code="rate_limit", message="Too many requests"),
    "/retry\r",
    turn_started(),
    assistant_msg("Hi there."),
    turn_completed(),
    take_snapshot(),
]
