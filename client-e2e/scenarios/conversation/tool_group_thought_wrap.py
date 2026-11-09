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

# The folded group header lands on SGR row 11, then its `⏵ Thought` row on 12.
_CLICK_GROUP = "\x1b[<0;1;11M\x1b[<0;1;11m"
_CLICK_THOUGHT = "\x1b[<0;5;12M\x1b[<0;5;12m"

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
