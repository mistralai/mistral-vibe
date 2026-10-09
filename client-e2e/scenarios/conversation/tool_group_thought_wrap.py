"""A long thought expanded inside a tool group wraps under its border and indent."""

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

_PROMPT = "run a command"

_THOUGHT = (
    "First I need to check what the command prints, so I will run it once and "
    "then compare its output against the expected value before answering the user.\n"
    "Short second line."
)

# The folded group header lands on SGR row 15, then its `⏵ Thought` row on 16.
_CLICK_GROUP = "\x1b[<0;1;15M\x1b[<0;1;15m"
_CLICK_THOUGHT = "\x1b[<0;5;16M\x1b[<0;5;16m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    reasoning(_THOUGHT),
    bash("echo one", "one"),
    assistant_msg("Done."),
    turn_completed(),
    _CLICK_GROUP,
    _CLICK_THOUGHT,
]
