"""A failed rewind read toasts the server's reason and leaves rewind mode."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi", entry_id="user-hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    _ESCAPE * 2,
]

handshake = {
    "session/rewind/read": {
        "error": {
            "code": "not_found",
            "message": "Rewindable history entry not found: stale",
        }
    }
}
request_methods = {"session/rewind/read"}

capture_steps = {1}
screen_contains = ("Rewindable history entry not found: stale",)
screen_excludes = ("Rewind to:", "Edit message from here")
