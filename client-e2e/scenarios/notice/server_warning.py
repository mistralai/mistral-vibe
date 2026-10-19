"""A server-pushed warning surfaces as a toast (VIBE-4622)."""

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
    server_warning("Skill 'demo' failed to load"),
]

screen_contains = ("Skill 'demo' failed to load",)
