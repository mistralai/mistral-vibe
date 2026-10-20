"""After `/resume` adopts a saved session, the next prompt is enqueued on that session."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"
_UP = "\x1b[A"

request_methods = {"session/resume"}
# Up highlights the saved session above the current one; Enter resumes it.
timeline: Timeline = [
    "/resume",
    "\r",
    _UP,
    "\r",
    "hi\r",
    turn_started(session_id=_SAVED),
    user_msg("hi", session_id=_SAVED),
    assistant_msg("Reply in the resumed session.", session_id=_SAVED),
    turn_completed(session_id=_SAVED),
]

capture_startup = False
capture_steps = {3, 4}
screen_contains = ("Saved session restored.", "Reply in the resumed session.")
