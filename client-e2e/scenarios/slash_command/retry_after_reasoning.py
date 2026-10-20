"""A /retry whose turn opens with reasoning keeps the new answer as its own assistant row."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
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

screen_contains = ("partial", "Thought", "recovered")
screen_excludes = ("partialrecovered", "Upstream 502", "/retry")

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("partial", generation_status="in_progress"),
    turn_failed(code="backend_error", message="Upstream 502"),
    "/retry\r",
    turn_started(),
    reasoning("thinking"),
    assistant_msg("recovered"),
    turn_completed(),
]
