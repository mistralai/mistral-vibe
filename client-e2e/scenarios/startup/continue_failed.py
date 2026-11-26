"""A `--continue` failing for another reason than nothing to continue shows the error and keeps the fresh session for prompts."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"

handshake = {
    "session/continue": {
        "error": {
            "code": "internal_error",
            "message": "generation was read without its published state",
            "data": {
                "harnessCode": "invalid_session_store",
                "details": {"session_id": _SAVED},
            },
        }
    }
}
client_args = ("--continue",)
request_methods = {"session/continue"}
timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Reply in a fresh session."),
    turn_completed(),
]

screen_contains = (
    "Failed to resume session: generation was read without its published state",
    "Reply in a fresh session.",
)
screen_excludes = ("No previous sessions found.", "Saved session restored.")
