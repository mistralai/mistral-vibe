"""Picking a session in the startup picker sends the positional prompt to that session."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"

client_args = ("continue the work", "--resume")
request_methods = {"session/resume"}
timeline: Timeline = [
    "\r",
    turn_started(session_id=_SAVED),
    user_msg("continue the work", session_id=_SAVED),
    assistant_msg("Continuing the saved work.", session_id=_SAVED),
    turn_completed(session_id=_SAVED),
]

screen_contains = ("Resumed session 00000000", "Continuing the saved work.")
