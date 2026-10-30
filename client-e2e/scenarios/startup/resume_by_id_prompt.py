"""After `--resume <id>` attaches, a submitted prompt is enqueued on the resumed session."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"

client_args = ("--resume", _SAVED)
timeline: Timeline = [
    "hi\r",
    turn_started(session_id=_SAVED),
    user_msg("hi", session_id=_SAVED),
    assistant_msg("Reply in the resumed session.", session_id=_SAVED),
    turn_completed(session_id=_SAVED),
]

screen_contains = ("Resumed session 00000000", "Reply in the resumed session.")
screen_excludes = ("Resuming session",)
