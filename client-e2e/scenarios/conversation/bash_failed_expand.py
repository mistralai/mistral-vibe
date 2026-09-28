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
# The group header, then the shell result row it reveals: shell output folds twice.
_EXPAND_GROUP = "\x1b[<0;1;30M\x1b[<0;1;30m"
_EXPAND_RESULT = "\x1b[<0;8;30M\x1b[<0;8;30m"
_ERROR = "Command exited with status 1"

screen_contains = {"python": (f"Error: {_ERROR}",), "rust": (f"Error: {_ERROR}",)}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash_failed("false", _ERROR),
    assistant_msg("The command failed."),
    turn_completed(),
    _EXPAND_GROUP,
    _EXPAND_RESULT,
]
