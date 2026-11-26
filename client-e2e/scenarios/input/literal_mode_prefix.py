"""A mode character typed before prompt text or after leading whitespace stays message text."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

request_methods = {"workspace/prompt/prepare", "telemetry/record"}

_UP = "\x1b[A"

# The recalled entry keeps its leading space, so it reloads as a prompt.
timeline: Timeline = [
    "config",
    "\x01/",
    "\r",
    turn_started(),
    user_msg("/config"),
    assistant_msg("That was a message."),
    turn_completed(),
    " /help",
    "\r",
    turn_started(),
    user_msg(" /help"),
    assistant_msg("So was that."),
    turn_completed(),
    _UP,
]
