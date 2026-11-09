"""`/copy` copies the last agent message, confirms it, and records the copy."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
request_methods = {"telemetry/record"}
capture_startup = False
expected_clipboard = "The answer is 42."
screen_contains = ("Last agent message copied to clipboard",)

timeline: Timeline = [
    "question\r",
    turn_started(),
    user_msg("question"),
    assistant_msg("An earlier draft."),
    assistant_msg("The answer is 42."),
    turn_completed(),
    "/copy\r",
]
