"""Expanded shell output strips terminal controls and collapses carriage returns."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "show safe output"
# The group header, then the shell result row it reveals: shell output folds twice.
_EXPAND_GROUP = "\x1b[<0;1;30M\x1b[<0;1;30m"
_EXPAND_RESULT = "\x1b[<0;8;30M\x1b[<0;8;30m"
_OUTPUT = "old\rfinal \x1b[31mred\x1b[0m \x1b]0;hidden\x07visible\x07\n"

screen_contains = {"python": ("final red visible",), "rust": ("final red visible",)}
screen_excludes = {"python": ("[31m", "]0;hidden"), "rust": ("[31m", "]0;hidden")}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("printf output", _OUTPUT),
    assistant_msg("Done."),
    turn_completed(),
    _EXPAND_GROUP,
    _EXPAND_RESULT,
]
