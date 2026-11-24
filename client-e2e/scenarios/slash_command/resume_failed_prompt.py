"""A failed open keeps the current session; after Esc, prompts still go to it."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, advance

_UP = "\x1b[A"

handshake = {
    "session/resume": {"error": {"code": "internal_error", "message": "Resume failed"}}
}
request_methods = {"session/resume"}
# Up highlights the saved session, Enter fails to resume it, Esc leaves the page.
timeline: Timeline = [
    "/resume",
    "\r",
    _UP,
    "\r",
    advance(1500),
    "\x1b[27u",
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Reply in the current session."),
    turn_completed(),
]

screen_contains = ("Reply in the current session.",)
screen_excludes = ("Saved session restored.", "Failed to resume session")
