from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    session_title_updated,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

expected_titles = ("Vibe", "A useful title")

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    session_title_updated("A useful title"),
    assistant_msg("Hello."),
    turn_completed(),
]
