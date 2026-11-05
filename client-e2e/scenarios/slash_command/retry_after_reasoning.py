"""A /retry whose turn opens with reasoning replaces the unfinished answer with the new reply."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    discard_entry,
    reasoning,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

handshake = {
    "config/read": {"config": {"showThinkingNodes": True}},
    "runtime/read": {"runtime": {"config": {"showThinkingNodes": True}}},
}

screen_contains = ("Thought", "recovered")
screen_excludes = ("partial", "Upstream 502", "/retry")

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("partial", entry_id="partial", generation_status="in_progress"),
    discard_entry("partial"),
    turn_failed(code="backend_error", message="Upstream 502"),
    "/retry\r",
    turn_started(),
    reasoning("thinking"),
    assistant_msg("recovered"),
    turn_completed(),
]
