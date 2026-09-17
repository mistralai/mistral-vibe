"""Consume the bad-rating key while the feedback prompt is active."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

handshake = {
    "feedback/record": {},
    "feedback/shouldShow": {"show": True, "snoozeDurationSeconds": 604800},
}

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "3",
]
