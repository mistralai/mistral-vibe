"""An expanded failed shell command shows its failure message."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash_failed,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "run a failing command"
# The lone shell call stays ungrouped, so its own header row unfolds the output.
_EXPAND_RESULT = "\x1b[<0;1;15M\x1b[<0;1;15m"
_ERROR = "Command exited with status 1"

screen_contains = {"python": (f"Error: {_ERROR}",), "rust": (f"Error: {_ERROR}",)}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash_failed("false", _ERROR),
    assistant_msg("The command failed."),
    turn_completed(),
    _EXPAND_RESULT,
]
