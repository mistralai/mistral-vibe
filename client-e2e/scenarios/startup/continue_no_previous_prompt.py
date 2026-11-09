"""`--continue` with no saved session says so, starts fresh, and the composer still sends prompts."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

handshake = {
    "session/continue": {
        "error": {
            "code": "not_found",
            "message": "No session to continue",
            "data": {"harnessCode": "nothing_to_continue"},
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

screen_contains = ("No previous sessions found.", "Reply in a fresh session.")
