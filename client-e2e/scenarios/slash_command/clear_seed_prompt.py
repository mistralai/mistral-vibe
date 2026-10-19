"""`/clear <prompt>` starts a fresh conversation, then sends the prompt to the new session."""

from __future__ import annotations

from e2e.app_server.events import (
    CLEARED_SESSION_ID,
    assistant_msg,
    cleared_state,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_first_turn = [
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
]
# The replay server numbers events as it emits them and the loader adds one
# queue drain per started turn, so the cleared state resumes after that many.
_cleared = cleared_state()
_cleared["eventId"] = len(_first_turn) + 1

request_methods = {"session/history/clear"}
handshake = {"session/history/clear": {"state": _cleared}}

timeline: Timeline = [
    "hi\r",
    *_first_turn,
    "/clear fix the tests\r",
    turn_started(session_id=CLEARED_SESSION_ID),
    user_msg("fix the tests", session_id=CLEARED_SESSION_ID),
    assistant_msg("Fixing them.", session_id=CLEARED_SESSION_ID),
    turn_completed(session_id=CLEARED_SESSION_ID),
]

capture_startup = False
screen_contains = ("> fix the tests", "Fixing them.")
screen_excludes = ("Hi. What do you need?",)
