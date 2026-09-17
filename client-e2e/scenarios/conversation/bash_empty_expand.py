"""An expanded shell command with no output shows the empty-content placeholder."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "run a quiet command"
_CLICK = "\x1b[<0;1;30M\x1b[<0;1;30m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("true"),
    assistant_msg("Done."),
    turn_completed(),
    _CLICK,
]
