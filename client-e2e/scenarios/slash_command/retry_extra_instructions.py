"""A case-variant /Retry with instructions passes them to the retried turn's prompt."""

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
    turn_failed(code="backend_error", message="Upstream 502"),
    "/Retry keep it brief\r",
    turn_started(),
    assistant_msg("Hi there."),
    turn_completed(),
    take_snapshot(),
]
