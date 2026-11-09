"""A failed `/resume` over a preview restores the current transcript and the composer still sends prompts."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_UP = "\x1b[A"

handshake = {
    "session/resume": {"error": {"code": "internal_error", "message": "Resume failed"}}
}
request_methods = {"session/resume"}
# Up previews the saved session, Enter fails to resume it, then a prompt goes to the current session.
timeline: Timeline = [
    "/resume",
    "\r",
    _UP,
    "\r",
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Reply in the current session."),
    turn_completed(),
]

capture_startup = False
capture_steps = {2, 3, 4}
screen_contains = (
    "Failed to resume session: Resume failed",
    "Reply in the current session.",
)
screen_excludes = ("Saved session restored.",)
