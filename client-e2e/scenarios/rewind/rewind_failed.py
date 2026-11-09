"""A rejected session/rewind shows the server's reason and keeps the conversation."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi", entry_id="user-hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "/rewind\r",
    "\r",
    "\r",
]

handshake = {
    "session/rewind/read": {"hasFileChanges": False, "paths": []},
    "session/rewind": {
        "error": {"code": "conflict", "message": "A turn is already running: turn-9"}
    },
}
request_methods = {"session/rewind"}

screen_contains = ("A turn is already running: turn-9", "Hi. What do you need?")
