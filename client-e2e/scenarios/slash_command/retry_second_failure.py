"""Two failures in a row: the successful retry replaces the unfinished answer and both errors."""

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

screen_contains = ("Hi there.",)
screen_excludes = ("Partial ans", "Upstream 502", "Upstream 503", "/retry")

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Partial ans", entry_id="partial", generation_status="in_progress"),
    discard_entry("partial"),
    turn_failed(code="backend_error", message="Upstream 502"),
    "/retry\r",
    turn_started(),
    turn_failed(code="backend_error", message="Upstream 503"),
    "/retry\r",
    turn_started(),
    assistant_msg("Hi there."),
    turn_completed(),
]
