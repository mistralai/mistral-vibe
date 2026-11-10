"""Session metadata and resume replace the current terminal title."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    session_title_updated,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

handshake = {
    "runtime/read": {"sessionLog": {"title": "Saved implementation"}},
    "session/start": {"state": {"session": {"title": "Saved implementation"}}},
}

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    session_title_updated("Current task"),
    assistant_msg("Hello."),
    turn_completed(),
    "/resume\r",
    "\x1b[A",
    "\r",
]

expected_titles = (
    "Saved implementation",
    "Current task",
    "Current task",
    "Current task",
    "Saved implementation",
)
