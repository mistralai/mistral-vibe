"""Two server warnings in one turn stack as separate toasts (VIBE-4622)."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    server_warning,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hello."),
    turn_completed(),
    server_warning("first warning"),
    server_warning("second warning"),
]

screen_contains = ("first warning", "second warning")
