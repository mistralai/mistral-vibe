"""A `/clear` the server rejects keeps the conversation the session still holds."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

request_methods = {"session/history/clear"}

handshake = {
    "session/history/clear": {
        "error": {"code": "internal_error", "message": "Session store unavailable"}
    }
}

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "/clear\r",
]

screen_contains = (
    "Failed to clear history: Session store unavailable",
    "Hi. What do you need?",
)
screen_excludes = ("New conversation started.",)
