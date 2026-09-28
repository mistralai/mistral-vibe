"""An expanded shell result with no output renders the no-content fallback."""

from __future__ import annotations

from e2e.app_server.events import bash, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "run the silent command"

# The settled disclosure header lands on SGR row 32.
_CLICK = "\x1b[<0;1;32M\x1b[<0;1;32m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("cd /tmp", stdout=""),
    turn_completed(),
    _CLICK,
]
