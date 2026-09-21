"""Clicking the collapsed group header expands its content with a gutter."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    reasoning,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

handshake = {
    "config/read": {"config": {"showThinkingNodes": True}},
    "runtime/read": {"runtime": {"config": {"showThinkingNodes": True}}},
}

_PROMPT = "run two commands"

# The folded group header lands on SGR row 30.
_CLICK = "\x1b[<0;1;30M\x1b[<0;1;30m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    reasoning("Two quick calls."),
    bash("echo one", "one"),
    bash("echo two", "two"),
    assistant_msg("Done."),
    turn_completed(),
    _CLICK,
]
